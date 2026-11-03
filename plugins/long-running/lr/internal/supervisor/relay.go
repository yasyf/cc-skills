package supervisor

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	relayCheckEvery = 30 * time.Second
	relayDownAfter  = 2 * time.Minute
	relayLane       = "desk-runner"
	relayLockFile   = "relay.lock"
	relayLogFile    = "lr-relay.log"
	failureLimit    = 300
)

type RelayKeeper struct {
	Lock   string
	Log    string
	Store  *store.Store
	Post   pass.Post
	Logger *slog.Logger

	down time.Time
}

func NewRelayKeeper(state, inbox string, s *store.Store, post pass.Post, logger *slog.Logger) *RelayKeeper {
	return &RelayKeeper{Lock: filepath.Join(state, relayLockFile), Log: relayLog(inbox), Store: s, Post: post, Logger: logger}
}

func relayLog(inbox string) string {
	return filepath.Join(filepath.Dir(inbox), relayLogFile)
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
	held, err := lockHeld(k.Lock)
	if err != nil {
		return err
	}
	if held {
		k.down = time.Time{}
		return nil
	}
	if k.down.IsZero() {
		k.down = now
	}
	if now.Sub(k.down) >= relayDownAfter {
		k.blocked(now)
	}
	return nil
}

func (k *RelayKeeper) blocked(now time.Time) {
	said := "its log is empty"
	if text, err := os.ReadFile(k.Log); err == nil {
		if lines := strings.Split(strings.TrimSpace(string(text)), "\n"); lines[len(lines)-1] != "" {
			said = lines[len(lines)-1]
		}
	}
	said = said[:min(failureLimit, len(said))]
	subject := "lr-relay-down:" + said
	cause := fmt.Sprintf("`lr relay run` has been down past launchd's restart window, so no desk order or Run mail is read until it is back: %s", said)
	raised, err := k.Store.Escalate(subject, cause, lanes.Root, now)
	if err == nil && raised {
		err = k.Post([]string{lanes.Root}, bus.Escalation{Subject: subject, Cause: cause})
	}
	if err != nil {
		k.Logger.Warn("the relay is down and could not say so", "said", said, "err", err)
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
