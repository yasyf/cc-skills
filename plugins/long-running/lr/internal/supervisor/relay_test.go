package supervisor

import (
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type keeperWorld struct {
	keeper *RelayKeeper
	posts  []bus.Body
}

func newKeeperWorld(t *testing.T) *keeperWorld {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	dir := t.TempDir()
	w := &keeperWorld{}
	w.keeper = &RelayKeeper{
		Lock: filepath.Join(dir, "relay.lock"), Log: filepath.Join(dir, "lr-relay.log"), Store: s,
		Post: func(_ []string, body bus.Body) error {
			w.posts = append(w.posts, body)
			return nil
		},
		Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	return w
}

func (w *keeperWorld) check(t *testing.T, at time.Time) {
	t.Helper()
	if err := w.keeper.Check(context.Background(), at); err != nil {
		t.Fatal(err)
	}
}

func (w *keeperWorld) hold(t *testing.T) *os.File {
	t.Helper()
	lock, err := os.OpenFile(w.keeper.Lock, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = lock.Close() })
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}
	return lock
}

var keeperStart = time.Date(2026, 11, 3, 4, 0, 0, 0, time.UTC)

func TestARelayHoldingItsLockIsUpAndSaysNothing(t *testing.T) {
	w := newKeeperWorld(t)
	w.hold(t)

	w.check(t, keeperStart)
	w.check(t, keeperStart.Add(2*relayDownAfter))

	if len(w.posts) != 0 {
		t.Fatalf("posts = %+v", w.posts)
	}
}

func TestARelayDownPastLaunchdsRestartWindowTellsRootOncePerErrorNamingItsLog(t *testing.T) {
	w := newKeeperWorld(t)
	if err := os.WriteFile(w.keeper.Log, []byte("first line\nthe orca app is gone\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	w.check(t, keeperStart)
	w.check(t, keeperStart.Add(relayDownAfter-time.Second))
	if len(w.posts) != 0 {
		t.Fatalf("told inside the launchd restart gap: %+v", w.posts)
	}
	w.check(t, keeperStart.Add(relayDownAfter))
	w.check(t, keeperStart.Add(2*relayDownAfter))

	if len(w.posts) != 1 {
		t.Fatalf("posts = %+v", w.posts)
	}
	told, isEscalation := w.posts[0].(bus.Escalation)
	if !isEscalation || !strings.Contains(told.Cause, "until it is back: the orca app is gone") {
		t.Fatalf("told = %+v", w.posts[0])
	}
}

func TestARelayBackInsideTheWindowStartsTheClockAgain(t *testing.T) {
	w := newKeeperWorld(t)

	w.check(t, keeperStart)
	lock := w.hold(t)
	w.check(t, keeperStart.Add(relayDownAfter/2))
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	w.check(t, keeperStart.Add(relayDownAfter))

	if len(w.posts) != 0 {
		t.Fatalf("posts = %+v", w.posts)
	}
}

func TestTheRelayIsItsOwnJobSoASupervisorReexecNeverTouchesIt(t *testing.T) {
	job := agent().RelayJob("/state/inbox/orca.md")

	if job.Label == agent().Job().Label || job.Label != RelayLabel("d1") {
		t.Fatal(job.Label)
	}
	if !slices.Equal(job.Arguments, []string{"/claude/long-running/current/bin/lr", "relay", "run", "--config", "/state/lr.json"}) {
		t.Fatal(job.Arguments)
	}
	if job.Log != "/state/inbox/lr-relay.log" {
		t.Fatal(job.Log)
	}
	rostered := agent()
	rostered.Roster = true
	if args := rostered.RelayJob("/state/inbox/orca.md").Arguments; args[len(args)-1] != "--roster" {
		t.Fatal(args)
	}
}
