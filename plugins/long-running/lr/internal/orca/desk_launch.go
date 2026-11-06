package orca

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/terminalcap"
)

const (
	cpuBusyLimit  = 0.9
	launchStall   = 60 * time.Second
	spriteReceipt = "worker-start.json"
	spritePrepare = "prepare.status"
	spriteStop    = "stop.status"
	spriteMade    = "allocation.json"
	spriteDown    = "sprite-down"
	laneEnv       = "CLAUDE_LONG_RUNNING_LANE"
	placeOrca     = "orca"
	placeSprite   = "sprite"
)

var (
	launchedLine = regexp.MustCompile(`(?m)^(\S+) (ready|unsupervised) task=\S+ dispatch=(\S+) terminal=(\S+) worktree=\S+$`)
	pinBranch    = regexp.MustCompile(`^(?:dev|(?:[\w.-]+/)*cc-remote-\d+\.\d+\.\d+)$`)
	spriteID     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)
	spriteModels = map[string]string{"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "sol": "gpt-6.1-sol", "codex": "gpt-6.1-sol", "astra": "gpt-6-astra"}
	cpuIdle      = regexp.MustCompile(`([\d.]+)% idle`)
	loadAverage  = regexp.MustCompile(`^\{ (\d+(?:\.\d+)?) `)
	capRefusal   = regexp.MustCompile(` failed terminal create: \S+ The host is already running as many terminals as Orca allows\b`)
)

type detached struct{ done atomic.Bool }

func (p *detached) Exited() bool { return p.done.Load() }

func SpawnDetached(argv []string, log string, env []string) (Process, error) {
	if err := os.MkdirAll(filepath.Dir(log), 0o750); err != nil {
		return nil, err
	}
	sink, err := os.OpenFile(log, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return nil, err
	}
	defer sink.Close()
	if err := syscall.Flock(int(sink.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return nil, fmt.Errorf("lock launch log %s: %w", log, err)
	}
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Stdout, cmd.Stderr = sink, sink
	cmd.Env = append(os.Environ(), env...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	process := &detached{}
	go func() {
		_ = cmd.Wait()
		process.done.Store(true)
	}()
	return process, nil
}

func launcherRunning(log string) bool {
	file, err := os.Open(log)
	if err != nil {
		return false
	}
	defer file.Close()
	return errors.Is(syscall.Flock(int(file.Fd()), syscall.LOCK_SH|syscall.LOCK_NB), syscall.EWOULDBLOCK)
}

func (d *Desk) capture(ctx context.Context, env []string, argv ...string) (string, int, error) {
	return d.captured(ctx, shell.Command{Argv: argv, Env: env})
}

func (d *Desk) captureIdempotent(ctx context.Context, argv ...string) (string, int, error) {
	return d.captured(ctx, shell.Command{Argv: argv, Idempotent: true})
}

func (d *Desk) captured(ctx context.Context, c shell.Command) (string, int, error) {
	result, err := d.Runner.Run(ctx, c)
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return "", 0, err
	}
	said := result.Stderr
	if strings.TrimSpace(said) == "" {
		said = result.Stdout
	}
	if err != nil {
		return said, exit.Code, nil
	}
	return result.Stdout, 0, nil
}

func (d *Desk) launchLog(container, key string) string {
	return filepath.Join(filepath.Dir(d.Config.Store), "desk-runner-launches", container+"-"+key+".out")
}

func (d *Desk) spriteLog(container, key string) string {
	return strings.TrimSuffix(d.launchLog(container, key), ".out") + ".sprite.out"
}

func (d *Desk) refusedLog(container, key string) string {
	return strings.TrimSuffix(d.launchLog(container, key), ".out") + ".refused.out"
}

func (d *Desk) launch(ctx context.Context, container, lane string, action incident.Action) error {
	spec, err := launchOf(action)
	if err != nil {
		return err
	}
	bound, err := d.boundForLaunch(ctx)
	if err != nil {
		return err
	}
	if !bound.Bound {
		return d.refuseLaunch(ctx, container, lane, action, "an unbound runner: "+bound.Why, "post the go again under a new key once the coordinator's Orca terminal has run `"+d.rebindCommand()+"`")
	}
	route, err := d.spriteRefusal(ctx, action.ActionID, lane, spec)
	if err != nil {
		return err
	}
	if route.refusal != "" {
		return d.refuseLaunch(ctx, container, lane, action, route.refusal, "post the go again under a new key once that is fixed")
	}
	if !route.remote {
		if err := d.loadCheck(ctx, container, lane, action, spec); err != nil {
			return err
		}
	}
	deadline := time.Time{}
	if action.Deadline != "" {
		if deadline, err = store.Parse(string(action.Deadline)); err != nil {
			return err
		}
	}
	started, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.StartBy(action.ActionID, d.Now(), deadline, "")
		return err
	})
	if err != nil || !started {
		return err
	}
	if route.remote {
		return d.launchSprite(container, lane, action, spec)
	}
	return d.launchLocal(container, lane, action, spec)
}

func envPairs(values map[string]string) []string {
	env := make([]string, 0, len(values))
	for _, name := range sortedStrings(values) {
		env = append(env, name+"="+values[name])
	}
	return env
}

func sortedStrings(values map[string]string) []string {
	names := make([]string, 0, len(values))
	for name := range values {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}

func (d *Desk) launchLocal(container, lane string, action incident.Action, spec launchTarget) error {
	env := append(envPairs(d.Config.LaunchEnv), d.sender...)
	env = append(env, "ORCA_LAUNCH_STATE="+d.Config.Receipts, "LAUNCH_ROLE="+spec.Role, laneEnv+"="+or(spec.By, coordinatorLane), stageKeyEnv+"="+action.ActionID, stageDriveEnv+"="+d.Config.Drive)
	if failureFallsBack(Order{Lane: lane, Model: spec.Model, Role: spec.Role}, d.briefText(spec.Brief)) && !isFile(d.spriteLog(container, action.ActionID)) {
		env = append(env, fallbackEnv+"=1")
	}
	process, err := d.Spawn([]string{d.tool("lr"), "launch", "start", lane, spec.Model, spec.Effort, spec.Brief}, d.launchLog(container, action.ActionID), env)
	if err != nil {
		return err
	}
	d.launching[threadOf(container, action.ActionID)] = process
	return nil
}

func (d *Desk) spriteConfig() (*spriteRoute, error) {
	file, err := readRunnerFile(d.Config.Source)
	if err != nil {
		return nil, err
	}
	return file.Orca.Sprite, nil
}

func (d *Desk) launchSprite(container, lane string, action incident.Action, spec launchTarget) error {
	sprite, err := d.spriteConfig()
	if err != nil {
		return err
	}
	env := append(envPairs(d.Config.LaunchEnv), d.sender...)
	env = append(env, "WORKER_RUN="+d.Config.Run, stageKeyEnv+"="+action.ActionID, stageDriveEnv+"="+d.Config.Drive)
	env = append(env, envPairs(sprite.Env)...)
	tools, err := json.Marshal(sprite.Tools)
	if err != nil {
		return err
	}
	env = append(env, "WORKER_LOCAL=", "WORKER_NOTES_REPO="+d.Config.BriefsRepo, "WORKER_TOOLS="+string(tools), setupEnv+"="+sprite.Setup, workerDriveDir+"="+filepath.Dir(d.Config.Source), workerLandingRepo+"="+d.Config.LandingRepo)
	model := spec.Model
	if mapped, found := spriteModels[model]; found {
		model = mapped
	}
	process, err := d.Spawn([]string{sprite.Launcher, lane, model, spec.Effort, spec.Brief}, d.spriteLog(container, action.ActionID), env)
	if err != nil {
		return err
	}
	key := threadOf(container, action.ActionID)
	d.launching[key] = process
	d.sprites[key] = true
	return nil
}

func (d *Desk) laneBy(lane string) (string, error) {
	launches, err := d.laneActions(laneBook(lane), kindLaunch)
	if err != nil || len(launches) == 0 {
		return "", err
	}
	spec, err := launchOf(launches[len(launches)-1])
	if err != nil {
		return "", err
	}
	if spec.By == coordinatorLane {
		return "", nil
	}
	return verdictTo(spec.By), nil
}

func verdictTo(by string) string {
	if slices.Contains([]string{"", deskRunner, supervisorLane, "stall-watch"}, by) {
		return ""
	}
	return by
}

type launchRoute struct {
	remote  bool
	refusal string
}

func (d *Desk) spriteRefusal(ctx context.Context, key, lane string, spec launchTarget) (launchRoute, error) {
	raw, err := os.ReadFile(spec.Brief)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return launchRoute{}, err
	}
	text := string(raw)
	over, remote, err := d.onSprite(ctx, key, lane, spec.Model, text)
	if err != nil || !remote {
		return launchRoute{}, err
	}
	fault, err := d.spriteFault(ctx, lane)
	if err != nil {
		return launchRoute{}, err
	}
	if briefDesk.MatchString(text) || over != "" {
		return launchRoute{remote: fault == ""}, nil
	}
	return launchRoute{remote: fault == "", refusal: fault}, nil
}

func (d *Desk) onSprite(ctx context.Context, key, lane, model, text string) (string, bool, error) {
	switch {
	case macRequired(lane, model, text):
		_, err := d.macOverload(ctx, key, d.overLoad)
		return "", false, err
	case !macPreferred(text):
		return "", true, nil
	}
	over, err := d.macOverload(ctx, key, d.overLoadOrFull)
	return over, over != "", err
}

func (d *Desk) loadPath(key string) string {
	return filepath.Join(filepath.Dir(filepath.Dir(d.Config.DeskInbox)), "launch-load", key)
}

func (d *Desk) macOverload(ctx context.Context, key string, read func(context.Context) string) (string, error) {
	path := d.loadPath(key)
	if raw, err := os.ReadFile(path); !errors.Is(err, os.ErrNotExist) {
		return string(raw), err
	}
	over := read(ctx)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return "", err
	}
	staged := fmt.Sprintf("%s.%d", path, os.Getpid())
	if err := os.WriteFile(staged, []byte(over), 0o600); err != nil {
		return "", err
	}
	defer func() { _ = os.Remove(staged) }()
	if err := os.Link(staged, path); err != nil && !errors.Is(err, os.ErrExist) {
		return "", err
	}
	raw, err := os.ReadFile(path)
	return string(raw), err
}

func (d *Desk) overLoadOrFull(ctx context.Context) string {
	if over := d.overLoad(ctx); over != "" {
		return over
	}
	return d.noTerminalRoom(ctx)
}

func (d *Desk) overLoad(ctx context.Context) string {
	said, _, err := d.capture(ctx, nil, "sysctl", "-n", "vm.loadavg")
	found := loadAverage.FindStringSubmatch(said)
	if err != nil || found == nil {
		return ""
	}
	load, _ := strconv.ParseFloat(found[1], 64)
	if load <= float64(d.Config.LoadLimit) {
		return ""
	}
	return fmt.Sprintf("Mac load %.0f over %d", load, d.Config.LoadLimit)
}

func (d *Desk) noTerminalRoom(ctx context.Context) string {
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	reading, err := terminalcap.Read(ctx, d.Runner, home)
	if err != nil || !reading.Full() {
		return ""
	}
	return fmt.Sprintf("Orca holds all %d terminals it allows on this Mac", reading.Cap)
}

func (d *Desk) spriteFault(ctx context.Context, lane string) (string, error) {
	sprite, err := d.spriteConfig()
	if err != nil {
		return "", err
	}
	if sprite == nil {
		return "a runner config with no orca.sprite", nil
	}
	if !spriteLane.MatchString(lane) {
		return "lane " + lane + ", not a Sprite name of up to 55 lowercase letters, digits, and dashes", nil
	}
	for _, name := range []string{"WORKER_CC_REMOTE", "CC_REMOTE_CONFIG"} {
		path := sprite.Env[name]
		if path == "" {
			continue
		}
		dir := filepath.Dir(path)
		said, _, err := d.capture(ctx, nil, "git", "-C", dir, "branch", "--show-current")
		if err != nil {
			return "", err
		}
		branch := strings.TrimSpace(said)
		pinned := pinBranch.MatchString(branch)
		if !pinned && branch == "" {
			_, code, err := d.capture(ctx, nil, "git", "-C", dir, "merge-base", "--is-ancestor", "HEAD", "origin/dev")
			if err != nil {
				return "", err
			}
			pinned = code == 0
		}
		if !pinned {
			on := "no branch"
			if branch != "" {
				on = "branch " + branch
			}
			return fmt.Sprintf("%s %s, whose checkout is on %s, not dev, a cc-remote pin branch or a commit origin/dev holds; point it at a worktree that moves only with a pin PR", name, path, on), nil
		}
		said, code, err := d.capture(ctx, nil, "git", "-C", dir, "status", "--porcelain", "--untracked-files=no")
		if err != nil {
			return "", err
		}
		if code != 0 {
			return "", fmt.Errorf("git -C %s status exited %d: %s", dir, code, strings.TrimSpace(said))
		}
		var edited []string
		for line := range strings.Lines(said) {
			edited = append(edited, strings.TrimSpace(line[3:]))
		}
		if len(edited) > 0 {
			return fmt.Sprintf("%s %s, whose checkout has uncommitted edits to %s, so it runs no pinned commit; point it at a worktree that moves only with a pin PR", name, path, strings.Join(edited, ", ")), nil
		}
	}
	return "", nil
}

func (d *Desk) cpuBusy(ctx context.Context) (float64, error) {
	said, _, err := d.capture(ctx, nil, "top", "-l", "2", "-n", "0", "-s", "1")
	if err != nil {
		return 0, err
	}
	found := cpuIdle.FindAllStringSubmatch(said, -1)
	if len(found) == 0 {
		return 0, nil
	}
	idle, err := strconv.ParseFloat(found[len(found)-1][1], 64)
	if err != nil {
		return 0, err
	}
	return 1 - idle/100, nil
}

func (d *Desk) loadCheck(ctx context.Context, container, lane string, action incident.Action, spec launchTarget) error {
	busy, err := d.cpuBusy(ctx)
	if err != nil || busy < cpuBusyLimit {
		return err
	}
	held := fmt.Sprintf("CPU %.0f%% busy over the last minute", busy*100)
	watchdog := watchdogLane + "@" + d.Config.Drive
	if err := d.escalateTo(ctx, "load:"+store.Stamp(d.Now())[:15], "LOAD-HIGH", "runner", held+": while it lasts a desk launch starts on a Sprite and a lane pinned to the Mac starts here anyway, where a worktree checkout can time out. drive-watchdog: move the load off this Mac now; a pass, backfill, dry run, or bulk rewrite runs on in-region compute", watchdog); err != nil {
		return err
	}
	if spec.Model != agentIncident {
		return nil
	}
	return d.escalateTo(ctx, container+"/"+action.ActionID+":load", "LOAD-INCIDENT", lane, action.ActionID+" launches at "+held+": an incident fix lane is never held for load", watchdog)
}

func (d *Desk) refuseLaunch(ctx context.Context, container, lane string, action incident.Action, held, remedy string) error {
	spec, err := launchOf(action)
	if err != nil {
		return err
	}
	text := fmt.Sprintf("%s refused on %s; nothing was launched; %s", action.ActionID, held, remedy)
	failed, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(action.ActionID, text)
		return err
	})
	if err != nil || !failed {
		return err
	}
	return d.escalateTo(ctx, container+"/"+action.ActionID, "LAUNCH-FAILED", lane, text, verdictTo(spec.By))
}

func (d *Desk) stalledLaunches(ctx context.Context) error {
	books, err := d.workBooks()
	if err != nil {
		return err
	}
	for _, container := range books {
		lane := strings.TrimPrefix(container, lanePrefix)
		accepted, err := d.store.Actions(container, kindLaunch, incident.ActionAccepted)
		if err != nil {
			return err
		}
		for _, action := range accepted {
			if err := d.stalledLaunch(ctx, container, lane, action); err != nil {
				return err
			}
		}
	}
	return nil
}

func (d *Desk) stalledLaunch(ctx context.Context, container, lane string, action incident.Action) error {
	since, err := store.Parse(action.AcceptedAt)
	if err != nil {
		return err
	}
	age := d.Now().Sub(since)
	key := "stalled:" + container + "/" + action.ActionID
	if age < launchStall {
		return nil
	}
	if held, err := d.known(deskRunner, escalationPref+key); err != nil || held != nil {
		return err
	}
	spec, err := launchOf(action)
	if err != nil {
		return err
	}
	busy, err := d.cpuBusy(ctx)
	if err != nil {
		return err
	}
	return d.escalate(key, "LAUNCH-STALLED", lane, fmt.Sprintf("%s has no dispatch %d s after it was accepted, with the CPU %.0f%% busy over the last minute; withdraw it with `orca-desk: withdraw %s`, then launch it by hand: `lr launch start %s %s %s %s`",
		action.ActionID, int(age.Seconds()), busy*100, action.ActionID, lane, spec.Model, spec.Effort, spec.Brief))
}

func (d *Desk) reap(ctx context.Context) error {
	books, err := d.workBooks()
	if err != nil {
		return err
	}
	for _, container := range books {
		lane := strings.TrimPrefix(container, lanePrefix)
		started, err := d.store.Actions(container, kindLaunch, incident.ActionStarted)
		if err != nil {
			return err
		}
		for _, action := range started {
			if err := d.reapOne(ctx, container, lane, action); err != nil {
				return err
			}
		}
	}
	return nil
}

func isFile(path string) bool {
	info, err := os.Stat(path)
	return err == nil && !info.IsDir()
}

func (d *Desk) reapOne(ctx context.Context, container, lane string, action incident.Action) error {
	key := threadOf(container, action.ActionID)
	process := d.launching[key]
	if process != nil && !process.Exited() {
		return nil
	}
	if process == nil && (launcherRunning(d.launchLog(container, action.ActionID)) || launcherRunning(d.spriteLog(container, action.ActionID))) {
		return nil
	}
	delete(d.launching, key)
	sprite := d.sprites[key] || (isFile(d.spriteLog(container, action.ActionID)) && !isFile(d.launchLog(container, action.ActionID)))
	delete(d.sprites, key)
	if sprite {
		return d.settleSprite(ctx, container, lane, action, process != nil)
	}
	return d.settleLaunch(ctx, container, lane, action, placeOrca, "")
}

func spriteAttempt(brief string) string {
	name := filepath.Base(brief)
	if stem, _, found := cutLast(name, "."); found {
		name = stem
	}
	return filepath.Join(filepath.Dir(brief), name+".worker")
}

func neverStarted(attempt string, exited bool) bool {
	if _, err := os.Lstat(filepath.Join(attempt, spriteReceipt)); err == nil {
		return false
	}
	raw, _ := os.ReadFile(filepath.Join(attempt, spritePrepare))
	prepared := strings.TrimSpace(string(raw))
	stopped, _ := os.ReadFile(filepath.Join(attempt, spriteStop))
	return (prepared != "" && prepared != "0") || (exited && prepared == "") || strings.TrimSpace(string(stopped)) == "0"
}

func spriteStartedAs(receipt string) string {
	raw, err := os.ReadFile(receipt)
	if err != nil {
		return ""
	}
	var reply struct {
		Result map[string]any `json:"result"`
	}
	if json.Unmarshal(raw, &reply) != nil {
		return ""
	}
	ids := make([]string, 0, 2)
	for _, pair := range [][2]string{{"task", "taskId"}, {"dispatch", "dispatchId"}} {
		if value, isText := reply.Result[pair[1]].(string); isText && spriteID.MatchString(value) {
			ids = append(ids, pair[0]+"="+value)
		}
	}
	if len(ids) == 0 {
		return ""
	}
	return " as " + strings.Join(ids, " ")
}

type spriteReady struct {
	Task      string `json:"task"`
	Dispatch  string `json:"dispatch"`
	Terminal  string `json:"terminal"`
	Worktree  string `json:"worktree"`
	Workspace string `json:"workspace"`
}

func (r spriteReady) whole() bool {
	return r.Task != "" && r.Dispatch != "" && r.Terminal != "" && r.Worktree != "" && r.Workspace != ""
}

func (d *Desk) settleSprite(ctx context.Context, container, lane string, action incident.Action, exited bool) error {
	raw, err := os.ReadFile(d.spriteLog(container, action.ActionID))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	out := strings.TrimSpace(string(raw))
	if out == "" {
		return d.settleLaunch(ctx, container, lane, action, placeSprite, "")
	}
	spec, err := launchOf(action)
	if err != nil {
		return err
	}
	attempt := spriteAttempt(spec.Brief)
	lines := strings.Split(out, "\n")
	var ready spriteReady
	decoded := json.Unmarshal([]byte(lines[len(lines)-1]), &ready)
	receipt, readErr := os.ReadFile(filepath.Join(attempt, spriteReceipt))
	if decoded != nil || readErr != nil || !ready.whole() {
		return d.unsettledSprite(ctx, container, lane, action, spec, attempt, exited, out)
	}
	if err := os.MkdirAll(d.Config.Receipts, 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(d.receiptPath(lane, ".json"), receipt, 0o600); err != nil {
		return err
	}
	if err := os.WriteFile(d.receiptPath(lane, ".terminal"), []byte(ready.Terminal+"\n"), 0o600); err != nil {
		return err
	}
	line := fmt.Sprintf("%s ready task=%s dispatch=%s terminal=%s worktree=%s", lane, ready.Task, ready.Dispatch, ready.Terminal, ready.Worktree)
	if err := os.MkdirAll(filepath.Dir(d.launchLog(container, action.ActionID)), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(d.launchLog(container, action.ActionID), []byte(line+"\n"), 0o600); err != nil {
		return err
	}
	if err := d.settleLaunch(ctx, container, lane, action, placeSprite, ready.Workspace); err != nil {
		return err
	}
	installed, err := os.ReadFile(filepath.Join(attempt, "install.log"))
	if errors.Is(err, os.ErrNotExist) {
		return d.raiseDrift(lane, attempt)
	}
	if err != nil {
		return err
	}
	if err := d.Registry.RecordSpriteTools(ready.Workspace, installedTools(string(installed)), d.Now()); err != nil {
		return err
	}
	return d.raiseDrift(lane, attempt)
}

func (d *Desk) raiseDrift(lane, attempt string) error {
	raw, err := os.ReadFile(filepath.Join(attempt, spriteMade))
	if err != nil {
		return err
	}
	var made struct {
		Drift []struct {
			Hook  string `json:"hook"`
			Error string `json:"error"`
		} `json:"captainHookDrift"`
	}
	if err := json.Unmarshal(raw, &made); err != nil {
		return err
	}
	return d.store.Edit(deskRunner, func(tx *incident.Tx) error {
		for _, drift := range made.Drift {
			sum := sha256.Sum256([]byte(drift.Hook + "\x00" + drift.Error))
			key := "hook-drift:" + hex.EncodeToString(sum[:8])
			text := fmt.Sprintf("hook %s does not load under the Captain Hook the Sprite payload pins, so its guard is off on every Sprite; pin a captainHook build that loads it in tools/cc-remote/inventory.yaml. Import error: %s", drift.Hook, drift.Error)
			if _, _, err := tx.AcceptDue(escalationPref+key, kindEscalation, escalationLine("HOOK-DRIFT", key, lane, text), deskRunner, d.Now(), time.Time{}); err != nil {
				return err
			}
		}
		key, differs, err := baseDrift(attempt)
		if err != nil || key == "" {
			return err
		}
		text := "a Sprite's packages differ from the base its payload captured (-payload +machine) and installed online once, about 10 seconds; machine side older is a stale Sprite and owes nothing, machine side newer on every new Sprite means the base moved and the payload needs a rebuild: " + clipRunes(differs, 200)
		_, _, err = tx.AcceptDue(escalationPref+key, kindEscalation, escalationLine("BASE-DRIFT", key, lane, text), deskRunner, d.Now(), time.Time{})
		return err
	})
}

func baseDrift(attempt string) (key, differs string, err error) {
	said, err := os.ReadFile(filepath.Join(attempt, "prepare.err"))
	if errors.Is(err, os.ErrNotExist) {
		return "", "", nil
	}
	if err != nil {
		return "", "", err
	}
	_, after, found := strings.Cut(string(said), "so they install online once")
	if !found {
		return "", "", nil
	}
	var lines []string
	for _, line := range strings.Split(after, "\n")[1:] {
		if !strings.HasPrefix(line, "+") && !strings.HasPrefix(line, "-") {
			break
		}
		lines = append(lines, line)
	}
	differs = strings.Join(lines, " ")
	sum := sha256.Sum256([]byte(differs))
	return "base-drift:" + hex.EncodeToString(sum[:8]), differs, nil
}

func (d *Desk) unsettledSprite(ctx context.Context, container, lane string, action incident.Action, spec launchTarget, attempt string, exited bool, out string) error {
	reason := clipRunes(collapsed(out[strings.LastIndex(out, "\n")+1:]), 300)
	raw, _ := os.ReadFile(filepath.Join(attempt, spriteDown))
	canFallBack := d.Config.Launches != launchesSprite && !isFile(d.refusedLog(container, action.ActionID))
	if key, down, _ := strings.Cut(string(raw), "\n"); key == action.ActionID && canFallBack {
		down = clipRunes(collapsed(down), 300)
		if stopped, _ := os.ReadFile(filepath.Join(attempt, spriteStop)); len(stopped) > 0 && strings.TrimSpace(string(stopped)) != "0" {
			down += "; its prepared Sprite did not stop and may still be held, receipts in " + attempt
		}
		return d.macFallback(ctx, container, lane, action, spec, "its Sprite launch failed on the platform: "+down)
	}
	if neverStarted(attempt, exited) {
		if canFallBack && incidentLaunch(Order{Lane: lane, Model: spec.Model, Role: spec.Role}, d.briefText(spec.Brief)) {
			return d.macFallback(ctx, container, lane, action, spec, "its Sprite never started a worker, receipts in "+attempt+": "+reason)
		}
		return d.refuseLaunch(ctx, container, lane, action, "a Sprite that never started a worker: "+reason, "its receipts are in "+attempt+"; post the go again under a new key")
	}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Lose(action.ActionID)
		return err
	}); err != nil {
		return err
	}
	started := spriteStartedAs(filepath.Join(attempt, spriteReceipt))
	return d.escalateTo(ctx, container+"/"+action.ActionID+":sprite", "SPRITE-UNVERIFIABLE", lane, fmt.Sprintf("%s does not launch locally: its Sprite launch is unverified and a remote worker may be running%s; receipts in %s, nothing retried or cleaned up: %s", action.ActionID, started, attempt, reason), verdictTo(spec.By))
}

func (d *Desk) macFallback(ctx context.Context, container, lane string, action incident.Action, spec launchTarget, why string) error {
	if !incidentLaunch(Order{Lane: lane, Model: spec.Model, Role: spec.Role}, d.briefText(spec.Brief)) {
		if full := d.overLoadOrFull(ctx); full != "" {
			return d.escalateTo(ctx, container+"/"+action.ActionID+":mac-wait", "LAUNCH-WAITING", lane, fmt.Sprintf("%s waits to start on this Mac: %s, and %s; it starts here once that clears", action.ActionID, why, full), verdictTo(spec.By))
		}
	}
	if err := os.MkdirAll(filepath.Dir(d.loadPath(action.ActionID)), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(d.loadPath(action.ActionID), []byte(why), 0o600); err != nil {
		return err
	}
	return d.launchLocal(container, lane, action, spec)
}

func (d *Desk) settleLaunch(ctx context.Context, container, lane string, action incident.Action, placement, box string) error {
	key := action.ActionID
	raw, err := os.ReadFile(d.launchLog(container, key))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	out := strings.TrimSpace(string(raw))
	spec, err := launchOf(action)
	if err != nil {
		return err
	}
	found := launchedLine.FindStringSubmatch(out)
	receipt := d.receipt(lane)
	var proofLine, dispatch, terminal string
	failure := failureLine(out, lane)
	triedSprite := isFile(d.spriteLog(container, key))
	switch {
	case found != nil && found[1] == lane:
		proofLine, dispatch, terminal = found[0], found[3], found[4]
	case capRefusal.MatchString(failure) && !triedSprite && capFallsBack(Order{Lane: lane, Model: spec.Model, Role: spec.Role}, d.briefText(spec.Brief)):
		return d.spriteFallback(ctx, container, lane, action, spec, failure, "Orca refused it a terminal on this Mac, which runs as many as Orca allows")
	case failure != "" && !triedSprite && failureFallsBack(Order{Lane: lane, Model: spec.Model, Role: spec.Role}, d.briefText(spec.Brief)):
		return d.spriteFallback(ctx, container, lane, action, spec, failure, "its launch on this Mac failed ("+clipRunes(collapsed(failure), 200)+"), so it relaunched once on a Sprite")
	case failure != "":
		return d.failedLaunch(ctx, container, lane, key, failure, verdictTo(spec.By))
	case receipt != "" && receipt != spec.Prior:
		proofLine, dispatch, terminal = "receipt dispatch "+receipt, receipt, d.terminal(lane)
	default:
		if _, err := d.attempt(container, func(tx *incident.Tx) error {
			_, err := tx.Lose(key)
			return err
		}); err != nil {
			return err
		}
		said := "launch outcome unknown: no launch line and no new receipt; it was not relaunched"
		if out != "" {
			said += "; its log says: " + clipRunes(collapsed(out), 300)
		}
		return d.escalateTo(ctx, container+"/"+key, "UNVERIFIABLE", lane, said, verdictTo(spec.By))
	}
	proof := map[string]any{"line": proofLine, "dispatch": dispatch, "at": d.stamp()}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		if _, err := tx.Complete(key, proof, dispatch); err != nil {
			return err
		}
		_, err := tx.Verify(key, proof)
		return err
	}); err != nil {
		return err
	}
	value, marked, err := d.factOf(container, withdrawPending)
	if err != nil {
		return err
	}
	pending, _ := value.(map[string]any)
	withdrawing := marked && pending[kindLaunch] == key
	if !withdrawing {
		argv := []string{d.tool("drive.py"), "spawned", "--lane", lane, "--brief", spec.Brief, "--model", spec.Model, "--effort", spec.Effort, "--placement", placement}
		if spec.Role != "" {
			argv = append(argv, "--role", spec.Role)
		}
		said, code, err := d.capture(ctx, envPairs(d.Config.LaunchEnv), append(argv, "--by", or(spec.By, coordinatorLane))...)
		if err != nil {
			return err
		}
		if code != 0 {
			if err := d.escalate(container+"/"+key+":spawned", "SPAWN-UNRECORDED", lane, fmt.Sprintf("%s runs with no registry row, so the lane supervisor does not keep it alive: drive.py spawned exited %d: %.300s", key, code, strings.TrimSpace(said))); err != nil {
				return err
			}
		}
	}
	reading, err := os.ReadFile(d.loadPath(key))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	on := ""
	if box != "" {
		on = " on Sprite " + box
	}
	if len(reading) > 0 {
		on = or(on, " on this Mac") + ": " + string(reading)
	}
	if err := d.recordTo(ctx, escalationPref+container+"/"+key+":launched", fmt.Sprintf("LAUNCHED %s %s: dispatch %s terminal %s%s", key, lane, dispatch, terminal, on), verdictTo(spec.By)); err != nil {
		return err
	}
	if err := d.transfer(lane); err != nil {
		return err
	}
	if found != nil && found[2] == "unsupervised" {
		if err := d.escalate(container+"/"+key, "UNSUPERVISED", lane, "launched without Orca supervision: "+proofLine); err != nil {
			return err
		}
	}
	if !withdrawing {
		return nil
	}
	if err := d.store.Edit(container, func(tx *incident.Tx) error { return tx.DropFact(withdrawPending) }); err != nil {
		return err
	}
	by, _ := pending["by"].(string)
	return d.stopWithdrawn(ctx, "escalation:withdraw:"+container+"/"+key, by, key, lane, dispatch)
}

func (d *Desk) briefText(brief string) string {
	raw, _ := os.ReadFile(brief)
	return string(raw)
}

func (d *Desk) spriteFallback(ctx context.Context, container, lane string, action incident.Action, spec launchTarget, failure, reason string) error {
	fault, err := d.spriteFault(ctx, lane)
	if err != nil {
		return err
	}
	if fault != "" {
		return d.failedLaunch(ctx, container, lane, action.ActionID, failure+"; no Sprite took it instead: "+fault, verdictTo(spec.By))
	}
	if err := os.Rename(d.launchLog(container, action.ActionID), d.refusedLog(container, action.ActionID)); err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(d.loadPath(action.ActionID)), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(d.loadPath(action.ActionID), []byte(reason), 0o600); err != nil {
		return err
	}
	return d.launchSprite(container, lane, action, spec)
}

func failureLine(out, lane string) string {
	for _, line := range slices.Backward(strings.Split(out, "\n")) {
		if strings.HasPrefix(line, lane+" failed ") {
			return line
		}
	}
	return ""
}

func (d *Desk) failedLaunch(ctx context.Context, container, lane, key, reason, to string) error {
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(key, reason)
		return err
	}); err != nil {
		return err
	}
	return d.escalateTo(ctx, container+"/"+key, "LAUNCH-FAILED", lane, reason, to)
}
