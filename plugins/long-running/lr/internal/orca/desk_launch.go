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
)

const (
	cpuBusyLimit  = 0.9
	launchStall   = 60 * time.Second
	spriteReceipt = "worker-start.json"
	spritePrepare = "prepare.status"
	spriteMade    = "allocation.json"
	laneEnv       = "CLAUDE_LONG_RUNNING_LANE"
)

var (
	launchedLine = regexp.MustCompile(`(?m)^(\S+) (ready|unsupervised) task=\S+ dispatch=(\S+) terminal=(\S+) worktree=\S+$`)
	pinBranch    = regexp.MustCompile(`^(?:dev|(?:[\w.-]+/)*cc-remote-\d+\.\d+\.\d+)$`)
	spriteID     = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)
	spriteModels = map[string]string{"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "sol": "gpt-6.1-sol", "codex": "gpt-6.1-sol", "astra": "gpt-6-astra"}
	cpuIdle      = regexp.MustCompile(`([\d.]+)% idle`)
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

func (d *Desk) capture(ctx context.Context, env []string, argv ...string) (string, int, error) {
	result, err := d.Runner.Run(ctx, shell.Command{Argv: argv, Env: env})
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
		return d.refuseLaunch(container, lane, action, "an unbound runner: "+bound.Why, "the root launches it by hand with lr launch start, or runs `"+d.rebindCommand()+"` from its own terminal and submits it again under a new key")
	}
	route, err := d.spriteRefusal(ctx, lane, spec)
	if err != nil {
		return err
	}
	if route.refusal != "" {
		return d.refuseLaunch(container, lane, action, route.refusal, "the root submits it again under a new key once that is fixed")
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
	env = append(env, "ORCA_LAUNCH_STATE="+d.Config.Receipts, "LAUNCH_ROLE="+spec.Role, laneEnv+"="+or(spec.By, coordinatorLane))
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
	env = append(env, "WORKER_RUN="+d.Config.Run)
	env = append(env, envPairs(sprite.Env)...)
	tools, err := json.Marshal(sprite.Tools)
	if err != nil {
		return err
	}
	env = append(env, "WORKER_LOCAL=", "WORKER_NOTES_REPO="+d.Config.BriefsRepo, "WORKER_TOOLS="+string(tools))
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
	if slices.Contains([]string{"", coordinatorLane, deskRunner, supervisorLane, "stall-watch"}, spec.By) {
		return "", nil
	}
	return spec.By, nil
}

type launchRoute struct {
	remote  bool
	refusal string
}

func (d *Desk) spriteRefusal(ctx context.Context, lane string, spec launchTarget) (launchRoute, error) {
	raw, err := os.ReadFile(spec.Brief)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return launchRoute{}, err
	}
	text := string(raw)
	if macPinned(lane, spec.Model, text) {
		return launchRoute{}, nil
	}
	fault, err := d.spriteFault(ctx, lane)
	if err != nil {
		return launchRoute{}, err
	}
	if briefDesk.MatchString(text) {
		return launchRoute{remote: fault == ""}, nil
	}
	return launchRoute{remote: fault == "", refusal: fault}, nil
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
		said, _, err := d.capture(ctx, nil, "git", "-C", filepath.Dir(path), "branch", "--show-current")
		if err != nil {
			return "", err
		}
		branch := strings.TrimSpace(said)
		if pinBranch.MatchString(branch) {
			continue
		}
		on := "no branch"
		if branch != "" {
			on = "branch " + branch
		}
		return fmt.Sprintf("%s %s, whose checkout is on %s, not dev or a cc-remote pin branch; point it at a worktree that moves only with a pin PR", name, path, on), nil
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

func (d *Desk) refuseLaunch(container, lane string, action incident.Action, held, remedy string) error {
	text := fmt.Sprintf("%s refused on %s; nothing was launched, and %s", action.ActionID, held, remedy)
	failed, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(action.ActionID, text)
		return err
	})
	if err != nil || !failed {
		return err
	}
	return d.escalate(container+"/"+action.ActionID, "LAUNCH-FAILED", lane, text)
}

func (d *Desk) stalledLaunches(ctx context.Context) error {
	books, err := d.containers(lanePrefix)
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
	return d.escalate(key, "LAUNCH-STALLED", lane, fmt.Sprintf("%s accepted at %s has no dispatch %d s on, with the CPU %.0f%% busy over the last minute; withdraw it with `orca-desk: withdraw %s`, then launch it by hand: `lr launch start %s %s %s %s`",
		action.ActionID, pacificClock(since), int(age.Seconds()), busy*100, action.ActionID, lane, spec.Model, spec.Effort, spec.Brief))
}

func (d *Desk) reap(ctx context.Context) error {
	books, err := d.containers(lanePrefix)
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
	delete(d.launching, key)
	sprite := d.sprites[key] || (isFile(d.spriteLog(container, action.ActionID)) && !isFile(d.launchLog(container, action.ActionID)))
	delete(d.sprites, key)
	if sprite {
		return d.settleSprite(ctx, container, lane, action, process != nil)
	}
	return d.settleLaunch(ctx, container, lane, action, "")
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
	return (prepared != "" && prepared != "0") || (exited && prepared == "")
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
		return d.settleLaunch(ctx, container, lane, action, "")
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
		return d.unsettledSprite(container, lane, action, attempt, exited, out)
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
	argv := []string{d.tool("drive.py"), "spawned", "--lane", lane, "--brief", spec.Brief, "--model", spec.Model, "--effort", spec.Effort, "--placement", "sprite"}
	if spec.Role != "" {
		argv = append(argv, "--role", spec.Role)
	}
	said, code, err := d.capture(ctx, envPairs(d.Config.LaunchEnv), append(argv, "--by", or(spec.By, coordinatorLane))...)
	if err != nil {
		return err
	}
	if code != 0 {
		if err := d.escalate(container+"/"+action.ActionID+":spawned", "SPAWN-UNRECORDED", lane, fmt.Sprintf("%s runs on a Sprite with no registry row, so the lane supervisor does not keep it alive: drive.py spawned exited %d: %.300s", action.ActionID, code, strings.TrimSpace(said))); err != nil {
			return err
		}
	}
	if err := d.settleLaunch(ctx, container, lane, action, ready.Workspace); err != nil {
		return err
	}
	return d.raiseHookDrift(lane, attempt)
}

func (d *Desk) raiseHookDrift(lane, attempt string) error {
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
		return nil
	})
}

func (d *Desk) unsettledSprite(container, lane string, action incident.Action, attempt string, exited bool, out string) error {
	reason := clipRunes(collapsed(out), 300)
	if neverStarted(attempt, exited) {
		return d.refuseLaunch(container, lane, action, "a Sprite that never started a worker: "+reason, "its receipts are in "+attempt+"; the root submits it again under a new key")
	}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Lose(action.ActionID)
		return err
	}); err != nil {
		return err
	}
	started := spriteStartedAs(filepath.Join(attempt, spriteReceipt))
	return d.escalate(container+"/"+action.ActionID+":sprite", "SPRITE-UNVERIFIABLE", lane, fmt.Sprintf("%s does not launch locally: its Sprite launch is unverified and a remote worker may be running%s; receipts in %s, nothing retried or cleaned up: %s", action.ActionID, started, attempt, reason))
}

func (d *Desk) settleLaunch(ctx context.Context, container, lane string, action incident.Action, box string) error {
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
	switch {
	case found != nil && found[1] == lane:
		proofLine, dispatch, terminal = found[0], found[3], found[4]
	case out == "" && receipt != "" && receipt != spec.Prior:
		proofLine, dispatch, terminal = "receipt dispatch "+receipt, receipt, d.terminal(lane)
	case out != "":
		return d.failedLaunch(container, lane, key, out)
	default:
		if _, err := d.attempt(container, func(tx *incident.Tx) error {
			_, err := tx.Lose(key)
			return err
		}); err != nil {
			return err
		}
		return d.escalate(container+"/"+key, "UNVERIFIABLE", lane, "launch outcome unknown: no launch line and no new receipt; it was not relaunched")
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
	on := ""
	if box != "" {
		on = " on Sprite " + box
	}
	if err := d.record(escalationPref+container+"/"+key+":launched", fmt.Sprintf("LAUNCHED %s %s: dispatch %s terminal %s%s", key, lane, dispatch, terminal, on)); err != nil {
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
	return d.stopPendingWithdraw(ctx, container, lane, key, dispatch)
}

func (d *Desk) failedLaunch(container, lane, key, out string) error {
	reason := clipRunes(collapsed(out), 300)
	lines := strings.Split(out, "\n")
	for _, line := range slices.Backward(lines) {
		if strings.HasPrefix(line, lane+" failed ") {
			reason = line
			break
		}
	}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Fail(key, reason)
		return err
	}); err != nil {
		return err
	}
	return d.escalate(container+"/"+key, "LAUNCH-FAILED", lane, reason)
}

func (d *Desk) stopPendingWithdraw(ctx context.Context, container, lane, key, dispatch string) error {
	value, found, err := d.factOf(container, withdrawPending)
	if err != nil || !found {
		return err
	}
	pending, _ := value.(map[string]any)
	if pending[kindLaunch] != key {
		return nil
	}
	if err := d.store.Edit(container, func(tx *incident.Tx) error { return tx.DropFact(withdrawPending) }); err != nil {
		return err
	}
	by, _ := pending["by"].(string)
	return d.stopWithdrawn(ctx, "escalation:withdraw:"+container+"/"+key, by, key, lane, dispatch)
}
