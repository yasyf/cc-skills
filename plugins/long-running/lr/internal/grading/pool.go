package grading

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/capacity"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	localBox              = "local"
	logFile               = "job.log"
	pidFile               = "pid"
	sourcesFile           = "sources.md"
	capacityFile          = "grader-capacity.json"
	repostedFile          = "reposted"
	regradeMark           = "-regrade"
	failuresDir           = "sprite-failures"
	defaultConcurrent     = 8
	defaultTimeoutMinutes = 60
	maxAttempts           = 3
	headChars             = 12
	readTimeout           = 120 * time.Second
	failedHold            = 10 * time.Minute
	spriteFailingFor      = 10 * time.Minute
	deadlineCancel        = "its deadline"
)

var (
	ErrNoCapacity = errors.New("no grader capacity")
	ErrAnswered   = errors.New("no pass to run")

	proofTitle = regexp.MustCompile(`(?i)\bproof:|\b(?:red|green) proof\b|\bproof, do not merge\b`)
	proofBody  = regexp.MustCompile(`(?im)^proof PR(?: for #\d+\b|\b.*\bnever merge).*$`)
)

type Stopped struct {
	Why string
}

func (s *Stopped) Error() string {
	return "no pass will grade this head: " + s.Why
}

func (s *Stopped) Unwrap() error {
	return ErrAnswered
}

type names []string

func (n *names) UnmarshalJSON(data []byte) error {
	var one string
	if err := json.Unmarshal(data, &one); err == nil {
		*n = names{one}
		return nil
	}
	return json.Unmarshal(data, (*[]string)(n))
}

type Spec struct {
	Repo           string                `json:"repo"`
	Sprite         names                 `json:"sprite"`
	SpriteKeys     map[string][]string   `json:"sprite_keys"`
	Checkout       string                `json:"checkout"`
	Model          string                `json:"model"`
	Fast           *bool                 `json:"fast"`
	To             []string              `json:"to"`
	Concurrent     int                   `json:"concurrent"`
	Reserved       int                   `json:"reserved"`
	TimeoutMinutes int                   `json:"timeout_minutes"`
	Baseline       string                `json:"baseline"`
	Sources        Sources               `json:"sources"`
	ServiceTouches []grader.ServiceTouch `json:"service_touches"`
}

type Sources struct {
	Docs    map[string][]string `json:"docs"`
	Records map[string][]int64  `json:"records"`
}

type landing struct {
	Repo         string   `json:"repo"`
	Ledger       string   `json:"ledger"`
	Checkout     string   `json:"checkout"`
	PortPhrase   string   `json:"port_phrase"`
	ExemptGrades []string `json:"exempt_grades"`
	GoClasses    []string `json:"go_classes"`
}

type desk struct {
	spec Spec
	lane string
}

type Cursors interface {
	Cursor(source string) (int64, error)
	Advance(source string, position int64) error
}

type PoolDeps struct {
	Runner    shell.Runner
	Spawn     Spawn
	KillGroup func(group int) error
	Workers   func() ([]store.Worker, error)
	Cursors   Cursors
	Ledger    func(ledger string) (map[string]map[string]string, error)
	Picks     func() ([]store.Rule, error)
	Now       func() time.Time
	Log       *slog.Logger
	Self      string
	Scripts   string
	Config    string
}

type JobPool struct {
	PoolDeps
	drive     string
	root      string
	briefs    string
	landing   landing
	spriteCap *int
	tooling   string
	desks     map[string]desk
	executors int
	firsts    map[string]bool
	picks     []store.Rule

	cardUnread map[string]bool
}

type Spawn func(ctx context.Context, argv []string, env map[string]string, log string) (int, error)

func Detached(ctx context.Context, argv []string, env map[string]string, log string) (int, error) {
	out, err := os.OpenFile(log, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
	if err != nil {
		return 0, err
	}
	defer out.Close()
	cmd := exec.CommandContext(context.WithoutCancel(ctx), argv[0], argv[1:]...)
	cmd.Stdout, cmd.Stderr = out, out
	cmd.Env = os.Environ()
	for name, value := range env {
		cmd.Env = append(cmd.Env, name+"="+value)
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return 0, err
	}
	go func() { _ = cmd.Wait() }()
	return cmd.Process.Pid, nil
}

func NewJobPool(runner config.Runner, deps PoolDeps) (*JobPool, error) {
	p := &JobPool{PoolDeps: deps, drive: runner.Drive, root: filepath.Dir(runner.Source), briefs: runner.Orca.Briefs.Repo, desks: map[string]desk{}}
	if raw, on := runner.Sections["landing"]; on {
		if err := json.Unmarshal(raw, &p.landing); err != nil {
			return nil, fmt.Errorf("landing: %w", err)
		}
	}
	if raw, on := runner.Sections["sprite_cap"]; on {
		if err := json.Unmarshal(raw, &p.spriteCap); err != nil {
			return nil, fmt.Errorf("sprite_cap: %w", err)
		}
	}
	if raw, on := runner.Sections["tooling_lane"]; on {
		if err := json.Unmarshal(raw, &p.tooling); err != nil {
			return nil, fmt.Errorf("tooling_lane: %w", err)
		}
	}
	for grading, lane := range map[string]string{GraderFinder: grader.FinderLane, GraderFidelity: grader.FidelityLane} {
		raw, on := runner.Sections[grading]
		if !on {
			continue
		}
		spec := Spec{Concurrent: defaultConcurrent, TimeoutMinutes: defaultTimeoutMinutes}
		if err := json.Unmarshal(raw, &spec); err != nil {
			return nil, fmt.Errorf("%s: %w", grading, err)
		}
		p.desks[grading] = desk{spec, lane}
	}
	return p, nil
}

func (p *JobPool) repo() string {
	if d, on := p.desks[GraderFinder]; on && d.spec.Repo != "" {
		return d.spec.Repo
	}
	return p.landing.Repo
}

func (p *JobPool) Scale(queued int, now time.Time) error {
	limit := 0
	for _, d := range p.desks {
		limit = max(limit, len(d.spec.Sprite))
	}
	if limit == 0 {
		return nil
	}
	pool, err := p.plannedCapacity()
	if err != nil {
		return err
	}
	pool.Limit, pool.Concurrency = limit, defaultConcurrent
	if p.spriteCap != nil {
		pool.Concurrency = *p.spriteCap
	}
	planned := capacity.Plan(pool, float64(queued)*machine.GradeClock.Minutes(), now)
	p.executors = planned.Executors
	return writeAtomic(filepath.Join(p.root, capacityFile), planned)
}

func (p *JobPool) plannedCapacity() (capacity.Pool, error) {
	path := filepath.Join(p.root, capacityFile)
	pool := capacity.Pool{}
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return pool, nil
	}
	if err != nil {
		return pool, err
	}
	if err := json.Unmarshal(data, &pool); err != nil {
		return pool, fmt.Errorf("%s: %w", path, err)
	}
	return pool, nil
}

func (p *JobPool) Dispatch(ctx context.Context, grading string, facts machine.Facts) (string, error) {
	d, on := p.desks[grading]
	if !on {
		return "", ErrNoCapacity
	}
	pr := strconv.Itoa(facts.Number)
	workers, err := p.Workers()
	if err != nil {
		return "", err
	}
	if grading == GraderFinder {
		if err := p.cancelSuperseded(ctx, workers, pr, facts.Head); err != nil {
			return "", err
		}
	}
	return p.nextPass(ctx, grading, d, workers, facts, pr+"-"+short(facts.Head), nil)
}

func (p *JobPool) Regrade(ctx context.Context, facts machine.Facts, report []string) (string, error) {
	d, on := p.desks[GraderFidelity]
	if !on {
		return "", ErrNoCapacity
	}
	workers, err := p.Workers()
	if err != nil {
		return "", err
	}
	planned, err := p.plannedCapacity()
	if err != nil {
		return "", err
	}
	p.executors = planned.Executors
	base := strconv.Itoa(facts.Number) + "-" + short(facts.Head) + regradeMark
	return p.nextPass(ctx, GraderFidelity, d, workers, facts, base, &regrading{report: report})
}

func (p *JobPool) nextPass(ctx context.Context, grading string, d desk, workers []store.Worker, facts machine.Facts, base string, again *regrading) (string, error) {
	pr := strconv.Itoa(facts.Number)
	plan, err := p.nextAttempt(ctx, d, base)
	if err != nil {
		return "", err
	}
	if plan.running != "" {
		return plan.running, nil
	}
	if plan.held {
		return "", ErrAnswered
	}
	if tried := p.triedAttempts(d, base); again != nil && plan.settled != "" && tried < maxAttempts {
		plan = attemptPlan{number: tried + 1}
	}
	if plan.done {
		return "", p.answered(ctx, plan)
	}
	if again != nil {
		again.attempt = plan.number
	}
	sprite, err := p.place(d, workers, p.firsts[pr])
	if err != nil {
		return "", err
	}
	live := p.readPR(ctx, facts.Repo, pr)
	if proofTitle.MatchString(live.Title) || proofBody.MatchString(live.Body) {
		return "", p.tellProofSkip(ctx, facts, pr, live)
	}
	directory := filepath.Join(p.root, d.lane, attemptBase(base, plan.number))
	var built job
	switch grading {
	case GraderFinder:
		built, err = p.finderJob(ctx, d, facts, sprite, directory, live)
	case GraderFidelity:
		built, err = p.fidelityJob(ctx, d, facts, sprite, directory, live, again)
	}
	if errors.Is(err, ErrAnswered) {
		return "", &Stopped{"a record already names this head and no grade reached the store"}
	}
	if err != nil {
		return "", err
	}
	if built.answers, err = p.answersOwed(ctx, live, built.cited); err != nil {
		return "", err
	}
	return p.start(ctx, grading, d, sprite, directory, built)
}

func (p *JobPool) answered(ctx context.Context, plan attemptPlan) error {
	if plan.settled == "" {
		return &Stopped{plan.why}
	}
	directory := filepath.Join(p.root, plan.settled)
	if !exists(filepath.Join(directory, repostedFile)) {
		err := grader.Repost(ctx, directory, client.Post)
		var down *client.BusDown
		if errors.As(err, &down) {
			p.Log.Warn("the store did not take a settled pass's records; the next pass tries again", "job", directory, "error", err)
			return ErrAnswered
		}
		if err != nil {
			p.Log.Warn("a settled pass's records did not repost", "job", directory, "error", err)
		}
		if err := p.stamp(plan.settled, repostedFile); err != nil {
			return err
		}
		return ErrAnswered
	}
	waiting, err := p.stampedWithin(plan.settled, repostedFile, failedHold)
	if err != nil {
		return err
	}
	if waiting {
		return ErrAnswered
	}
	return &Stopped{settledWhy(directory)}
}

func settledWhy(directory string) string {
	lane := filepath.Base(filepath.Dir(directory))
	data, err := os.ReadFile(filepath.Join(directory, grader.ResultFile))
	if err != nil {
		return "its " + lane + " pass settled and its result does not read"
	}
	var outcome struct {
		Verdict string `json:"verdict"`
		Skipped string `json:"skipped"`
	}
	if err := json.Unmarshal(data, &outcome); err != nil {
		return "its " + lane + " pass settled and its result does not parse"
	}
	if outcome.Skipped != "" {
		return "its " + lane + " pass skipped: " + outcome.Skipped
	}
	if outcome.Verdict == readerVerdict {
		return "its " + lane + " pass handed the head to a reader, and no reader has posted a grade"
	}
	return fmt.Sprintf("its %s pass settled as %q and no grade reached the store", lane, outcome.Verdict)
}

func (p *JobPool) start(ctx context.Context, grading string, d desk, sprite, directory string, built job) (string, error) {
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return "", err
	}
	if built.sources != "" {
		if err := os.WriteFile(filepath.Join(directory, sourcesFile), []byte(built.sources), 0o600); err != nil {
			return "", err
		}
	}
	if err := os.WriteFile(filepath.Join(directory, grader.CardsFile), []byte(built.cards), 0o600); err != nil {
		return "", err
	}
	for path, body := range built.bodies {
		if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
			return "", err
		}
		if err := os.WriteFile(path, body, 0o600); err != nil {
			return "", err
		}
	}
	if err := writeAtomic(filepath.Join(directory, grader.AnswersFile), built.answers); err != nil {
		return "", err
	}
	if built.landed != nil {
		if err := writeAtomic(filepath.Join(directory, grader.LandedFile), built.landed); err != nil {
			return "", err
		}
	}
	if err := writeAtomic(filepath.Join(directory, grader.JobFile), built.spec); err != nil {
		return "", err
	}
	argv, env, err := p.command(d, grading, sprite, directory)
	if err != nil {
		return "", err
	}
	pid, err := p.Spawn(ctx, argv, env, filepath.Join(directory, logFile))
	if err != nil {
		return "", err
	}
	if err := writeAtomic(filepath.Join(directory, pidFile), pid); err != nil {
		return "", err
	}
	return workerName(sprite, d.lane, filepath.Base(directory)), nil
}

func (p *JobPool) command(d desk, grading, sprite, directory string) ([]string, map[string]string, error) {
	if sprite == "" {
		return []string{p.Self, "grade", grading, directory}, nil, nil
	}
	secrets := d.spec.SpriteKeys
	if secrets == nil {
		secrets = map[string][]string{}
	}
	keys, err := json.Marshal(secrets)
	if err != nil {
		return nil, nil, err
	}
	argv := []string{filepath.Join(p.Scripts, "sprite-job.sh"), sprite, grading, directory}
	return argv, map[string]string{"SPRITE_JOB_KEYS": string(keys)}, nil
}

func (p *JobPool) Settle(ctx context.Context, workers []string) (map[string]bool, error) {
	live := make(map[string]bool, len(workers))
	if len(workers) == 0 {
		return live, nil
	}
	commands, err := p.commands(ctx)
	if err != nil {
		return nil, err
	}
	states := make(map[string]jobState, len(workers))
	var running []string
	for _, name := range workers {
		ref := refOf(name)
		states[name] = stateOf(filepath.Join(p.root, ref.lane, ref.base), commands)
		if states[name] == jobRunning {
			running = append(running, name)
		}
	}
	stalled, err := p.stalls(ctx, running)
	if err != nil {
		return nil, err
	}
	for _, name := range workers {
		ref := refOf(name)
		directory := filepath.Join(p.root, ref.lane, ref.base)
		state := states[name]
		stopBy := ""
		if idle, stall := stalled[name]; stall {
			if err := p.markStalled(ref, directory, idle); err != nil {
				return nil, err
			}
			state, stopBy = jobStalled, stallCancel
		} else if state == jobRunning {
			late, err := p.timedOut(ref.lane, directory)
			if err != nil {
				return nil, err
			}
			if !late {
				live[name] = true
				continue
			}
			state, stopBy = jobFailed, deadlineCancel
		}
		if err := p.finished(ctx, ref, directory, state); err != nil {
			return nil, err
		}
		if stopBy != "" {
			if err := p.stop(ctx, ref, directory, stopBy); err != nil {
				return nil, err
			}
		}
		if err := grader.Repost(ctx, directory, client.Post); err != nil {
			p.Log.Warn("a settled pass's records did not reach the store; it stays a worker and the next settle posts them again", "job", directory, "error", err)
			live[name] = true
		}
	}
	return live, nil
}

func (p *JobPool) timedOut(lane, directory string) (bool, error) {
	d, on := p.desks[graderOf(lane)]
	if !on {
		return false, nil
	}
	info, err := os.Stat(filepath.Join(directory, pidFile))
	if err != nil {
		return false, err
	}
	running := p.Now().Sub(info.ModTime())
	if running < time.Duration(d.spec.TimeoutMinutes)*time.Minute {
		return false, nil
	}
	why := fmt.Sprintf("no result %d min after it started", int(running.Minutes()))
	return true, writeAtomic(filepath.Join(directory, grader.ResultFile), map[string]string{"error": why})
}

func graderOf(lane string) string {
	if lane == grader.FidelityLane {
		return GraderFidelity
	}
	return GraderFinder
}

type jobState int

const (
	jobRunning jobState = iota
	jobSettled
	jobFailed
	jobLost
	jobSkipped
	jobStalled
)

func stateOf(directory string, commands map[int]string) jobState {
	gone := !runsJob(directory, commands)
	data, err := os.ReadFile(filepath.Join(directory, grader.ResultFile))
	if err != nil {
		if gone {
			return jobLost
		}
		return jobRunning
	}
	var outcome struct {
		Error    *string `json:"error"`
		Lost     bool    `json:"no_result"`
		Stalled  bool    `json:"stalled"`
		Skipped  string  `json:"skipped"`
		Ungraded bool    `json:"ungraded"`
	}
	if err := json.Unmarshal(data, &outcome); err != nil || outcome.Lost {
		return jobLost
	}
	if outcome.Stalled {
		return jobStalled
	}
	if outcome.Error != nil {
		return jobFailed
	}
	if outcome.Skipped != "" && !outcome.Ungraded {
		return jobSkipped
	}
	return jobSettled
}

func runsJob(directory string, commands map[int]string) bool {
	said, err := os.ReadFile(filepath.Join(directory, pidFile))
	if err != nil {
		return false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(said)))
	return err == nil && strings.Contains(commands[pid], directory)
}

func (p *JobPool) commands(ctx context.Context) (map[int]string, error) {
	result, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"ps", "-A", "-ww", "-o", "pid=", "-o", "command="}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	commands := map[int]string{}
	for line := range strings.Lines(result.Stdout) {
		pid, command, found := strings.Cut(strings.TrimSpace(line), " ")
		number, err := strconv.Atoi(pid)
		if found && err == nil {
			commands[number] = command
		}
	}
	return commands, nil
}

type attemptPlan struct {
	number  int
	running string
	held    bool
	done    bool
	why     string
	settled string
}

func (p *JobPool) nextAttempt(ctx context.Context, d desk, base string) (attemptPlan, error) {
	tried := p.triedAttempts(d, base)
	if tried == 0 {
		return attemptPlan{number: 1}, nil
	}
	directory := filepath.Join(p.root, d.lane, attemptBase(base, tried))
	commands, err := p.commands(ctx)
	if err != nil {
		return attemptPlan{}, err
	}
	switch stateOf(directory, commands) {
	case jobRunning:
		sprite, err := jobSprite(directory)
		return attemptPlan{running: workerName(sprite, d.lane, filepath.Base(directory))}, err
	case jobSettled:
		return attemptPlan{done: true, settled: filepath.Join(d.lane, filepath.Base(directory))}, nil
	case jobSkipped:
		if strings.Contains(base, wholeMark) {
			return attemptPlan{done: true}, nil
		}
	case jobFailed:
		info, err := os.Stat(filepath.Join(directory, grader.ResultFile))
		if err != nil {
			return attemptPlan{}, err
		}
		if p.Now().Sub(info.ModTime()) < failedHold {
			return attemptPlan{held: true}, nil
		}
	case jobLost, jobStalled:
	}
	if tried >= maxAttempts {
		p.Log.Warn("a grader pass failed on its last attempt", "job", directory, "attempts", tried)
		why := fmt.Sprintf("its pass ended without a grade on all %d attempts: %s", tried, squeezed(failureOf(directory)))
		return attemptPlan{done: true, why: why}, p.exhausted(ctx, d, directory)
	}
	return attemptPlan{number: tried + 1}, nil
}

func (p *JobPool) triedAttempts(d desk, base string) int {
	tried := 0
	for tried < maxAttempts && exists(filepath.Join(p.root, d.lane, attemptBase(base, tried+1))) {
		tried++
	}
	return tried
}

func (p *JobPool) place(d desk, workers []store.Worker, first bool) (string, error) {
	running := map[string]int{}
	lanePasses := 0
	for _, worker := range workers {
		ref := refOf(worker.Name)
		running[ref.box]++
		if ref.lane == d.lane {
			lanePasses++
		}
	}
	open := d.spec.Concurrent
	if !first {
		open -= d.spec.Reserved
	}
	if lanePasses >= open {
		return "", ErrNoCapacity
	}
	if len(d.spec.Sprite) == 0 {
		return "", nil
	}
	taking, err := p.taking(d.spec.Sprite)
	if err != nil {
		return "", err
	}
	best := taking[0]
	for _, sprite := range taking[1:] {
		if running[sprite] < running[best] {
			best = sprite
		}
	}
	if p.spriteCap != nil && running[best] >= *p.spriteCap {
		return "", ErrNoCapacity
	}
	return best, nil
}

func (p *JobPool) taking(listed []string) ([]string, error) {
	healthy := make([]string, 0, len(listed))
	for _, sprite := range listed {
		failing, err := p.failing(sprite)
		if err != nil {
			return nil, err
		}
		if !failing {
			healthy = append(healthy, sprite)
		}
	}
	if len(healthy) == 0 {
		healthy = listed
	}
	return healthy[:min(len(healthy), max(p.executors, 1))], nil
}

func (p *JobPool) stamp(kind, sprite string) error {
	directory := filepath.Join(p.root, kind)
	if err := os.MkdirAll(directory, 0o750); err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(directory, sprite), []byte(p.Now().UTC().Format(time.RFC3339)))
}

func (p *JobPool) stampedWithin(kind, sprite string, window time.Duration) (bool, error) {
	data, err := os.ReadFile(filepath.Join(p.root, kind, sprite))
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	at, err := time.Parse(time.RFC3339, string(data))
	if err != nil {
		return false, err
	}
	return p.Now().Sub(at) < window, nil
}

func (p *JobPool) failing(sprite string) (bool, error) {
	return p.stampedWithin(failuresDir, sprite, spriteFailingFor)
}

func (p *JobPool) cancelSuperseded(ctx context.Context, workers []store.Worker, pr, head string) error {
	prefix := pr + "-"
	current := prefix + short(head)
	for _, worker := range workers {
		ref := refOf(worker.Name)
		if ref.lane != grader.FinderLane || !strings.HasPrefix(ref.base, prefix) || strings.HasPrefix(ref.base, current) || isWhole(worker.Name) {
			continue
		}
		if err := p.cancel(ctx, filepath.Join(p.root, ref.lane, ref.base), head); err != nil {
			return err
		}
	}
	return nil
}

func (p *JobPool) cancel(ctx context.Context, directory, by string) error {
	if err := os.WriteFile(filepath.Join(directory, grader.CancelledFile), []byte(by+"\n"), 0o600); err != nil {
		return err
	}
	runs, err := filepath.Glob(filepath.Join(directory, "codex-*"))
	if err != nil {
		return err
	}
	for _, run := range runs {
		if _, err := p.Runner.Run(ctx, shell.Command{Argv: []string{"codex-ask", "--cancel", run}, Timeout: readTimeout}); err != nil {
			p.Log.Warn("cancelling a codex run failed", "run", run, "error", err)
		}
	}
	return nil
}

func jobSprite(directory string) (string, error) {
	data, err := os.ReadFile(filepath.Join(directory, grader.JobFile))
	if err != nil {
		return "", err
	}
	var spec struct {
		Sprite string `json:"sprite"`
	}
	err = json.Unmarshal(data, &spec)
	return spec.Sprite, err
}

func attemptBase(base string, attempt int) string {
	if attempt > 1 {
		return base + "-" + strconv.Itoa(attempt)
	}
	return base
}

func workerName(sprite, lane, base string) string {
	box := sprite
	if box == "" {
		box = localBox
	}
	return box + "/" + lane + "/" + base
}

type workerRef struct {
	box  string
	lane string
	base string
}

func refOf(name string) workerRef {
	parts := strings.SplitN(name, "/", 3)
	if len(parts) != 3 {
		return workerRef{}
	}
	return workerRef{parts[0], parts[1], parts[2]}
}

func short(head string) string {
	return head[:min(headChars, len(head))]
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func writeAtomic(path string, value any) error {
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return writeFileAtomic(path, data)
}

func writeFileAtomic(path string, data []byte) error {
	staged, err := os.CreateTemp(filepath.Dir(path), ".staged-*")
	if err != nil {
		return err
	}
	_, err = staged.Write(data)
	if err = errors.Join(err, staged.Close()); err != nil {
		return errors.Join(err, os.Remove(staged.Name()))
	}
	return os.Rename(staged.Name(), path)
}
