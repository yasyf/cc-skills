package supervisor

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	relayCheckEvery = 5 * time.Second
	relayRespawn    = time.Minute
	relayStopWait   = 30 * time.Second
	relayLane       = "desk-runner"
	relayLockFile   = "relay.lock"
	relayLogFile    = "lr-relay.log"
	failureLimit    = 300
)

type relayChild interface {
	Exited() (code int, done bool)
	Stop(wait time.Duration)
}

type RelayKeeper struct {
	Argv   []string
	Lock   string
	Log    string
	Store  *store.Store
	Post   pass.Post
	Logger *slog.Logger
	Start  func(argv []string, log string) (relayChild, error)

	mu      sync.Mutex
	stopped bool
	child   relayChild
	started time.Time
}

func NewRelayKeeper(claude, config, state, inbox string, s *store.Store, post pass.Post, logger *slog.Logger) *RelayKeeper {
	return &RelayKeeper{
		Argv: []string{filepath.Join(claude, "long-running", "current", "bin", "lr"), "relay", "run", "--config", config},
		Lock: filepath.Join(state, relayLockFile), Log: filepath.Join(filepath.Dir(inbox), relayLogFile),
		Store: s, Post: post, Logger: logger, Start: startRelay,
	}
}

func (k *RelayKeeper) Keep(ctx context.Context, clock func() time.Time) {
	for ctx.Err() == nil {
		if err := k.Check(clock()); err != nil {
			k.Logger.Warn("relay check skipped", "err", err)
		}
		select {
		case <-ctx.Done():
		case <-time.After(relayCheckEvery):
		}
	}
}

func (k *RelayKeeper) Check(now time.Time) error {
	k.mu.Lock()
	defer k.mu.Unlock()
	if k.stopped || (!k.started.IsZero() && now.Sub(k.started) < relayRespawn) {
		return nil
	}
	held, err := lockHeld(k.Lock)
	if err != nil || held {
		return err
	}
	if k.child != nil {
		code, done := k.child.Exited()
		if !done {
			return nil
		}
		if code != 0 {
			k.failed(code, now)
		}
	}
	k.started = now
	k.child, err = k.Start(k.Argv, k.Log)
	return err
}

func (k *RelayKeeper) Stop() {
	k.mu.Lock()
	defer k.mu.Unlock()
	k.stopped = true
	if k.child != nil {
		k.child.Stop(relayStopWait)
	}
}

func (k *RelayKeeper) failed(code int, now time.Time) {
	said := fmt.Sprintf("exit %d", code)
	if text, err := os.ReadFile(k.Log); err == nil {
		if lines := strings.Split(strings.TrimSpace(string(text)), "\n"); lines[len(lines)-1] != "" {
			said = lines[len(lines)-1]
		}
	}
	said = said[:min(failureLimit, len(said))]
	subject := "lr-relay-failed:" + said
	cause := fmt.Sprintf("`lr relay run` exited %d, so no desk order is read until it restarts: %s", code, said)
	raised, err := k.Store.Escalate(subject, cause, lanes.Root, now)
	if err == nil && raised {
		err = k.Post([]string{lanes.Root}, bus.Escalation{Subject: subject, Cause: cause})
	}
	if err != nil {
		k.Logger.Warn("the relay failed and could not say so", "said", said, "err", err)
	}
}

func lockHeld(path string) (bool, error) {
	lock, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return false, err
	}
	defer func() { _ = lock.Close() }()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return true, nil
		}
		return false, err
	}
	return false, syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
}

type relayProcess struct {
	cmd  *exec.Cmd
	code atomic.Int64
	done atomic.Bool
}

func startRelay(argv []string, log string) (relayChild, error) {
	sink, err := os.OpenFile(log, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o600)
	if err != nil {
		return nil, err
	}
	defer func() { _ = sink.Close() }()
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Stdout, cmd.Stderr = sink, sink
	cmd.Env = append(os.Environ(), lanes.Identity(relayLane)...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	process := &relayProcess{cmd: cmd}
	go func() {
		_ = cmd.Wait()
		process.code.Store(int64(cmd.ProcessState.ExitCode()))
		process.done.Store(true)
	}()
	return process, nil
}

func (p *relayProcess) Exited() (int, bool) {
	return int(p.code.Load()), p.done.Load()
}

func (p *relayProcess) Stop(wait time.Duration) {
	if p.done.Load() {
		return
	}
	_ = p.cmd.Process.Signal(syscall.SIGTERM)
	for deadline := time.Now().Add(wait); time.Now().Before(deadline) && !p.done.Load(); {
		time.Sleep(time.Second)
	}
}
