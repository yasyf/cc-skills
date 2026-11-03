package supervisor

import (
	"context"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type fakeChild struct {
	code    int
	done    bool
	stopped int
}

func (c *fakeChild) Exited() (int, bool) { return c.code, c.done }

func (c *fakeChild) Stop(time.Duration) { c.stopped++ }

type keeperWorld struct {
	keeper *RelayKeeper
	starts []*fakeChild
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
		Argv: []string{"lr", "relay", "run"}, Lock: filepath.Join(dir, "relay.lock"), Log: filepath.Join(dir, "lr-relay.log"), Store: s,
		Post: func(_ []string, body bus.Body) error {
			w.posts = append(w.posts, body)
			return nil
		},
		Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
		Start: func([]string, string) (relayChild, error) {
			child := &fakeChild{}
			w.starts = append(w.starts, child)
			return child, nil
		},
	}
	return w
}

func (w *keeperWorld) check(t *testing.T, at time.Time) {
	t.Helper()
	if err := w.keeper.Check(context.Background(), at); err != nil {
		t.Fatal(err)
	}
}

var keeperStart = time.Date(2026, 11, 3, 4, 0, 0, 0, time.UTC)

func TestTheKeeperStartsOneRelayAndLeavesARunningOneAlone(t *testing.T) {
	w := newKeeperWorld(t)

	w.check(t, keeperStart)
	w.check(t, keeperStart.Add(time.Second))
	w.check(t, keeperStart.Add(2*relayRespawn))

	if len(w.starts) != 1 {
		t.Fatalf("starts = %d", len(w.starts))
	}
}

func TestARelayAnotherProcessHoldsIsLeftAlone(t *testing.T) {
	w := newKeeperWorld(t)
	lock, err := os.OpenFile(w.keeper.Lock, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = lock.Close() }()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		t.Fatal(err)
	}

	w.check(t, keeperStart)
	if len(w.starts) != 0 {
		t.Fatalf("started beside a held lock: %d", len(w.starts))
	}

	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_UN); err != nil {
		t.Fatal(err)
	}
	w.check(t, keeperStart.Add(time.Second))
	if len(w.starts) != 1 {
		t.Fatalf("starts after the lock came free = %d", len(w.starts))
	}
}

func TestACleanExitRestartsWithNoReport(t *testing.T) {
	w := newKeeperWorld(t)
	w.check(t, keeperStart)
	w.starts[0].done = true

	w.check(t, keeperStart.Add(time.Second))
	if len(w.starts) != 1 {
		t.Fatalf("restarted inside the respawn gap: %d", len(w.starts))
	}
	w.check(t, keeperStart.Add(relayRespawn))

	if len(w.starts) != 2 || len(w.posts) != 0 {
		t.Fatalf("starts = %d, posts = %+v", len(w.starts), w.posts)
	}
}

func TestAFailedExitTellsRootOncePerErrorAndRestarts(t *testing.T) {
	w := newKeeperWorld(t)
	if err := os.WriteFile(w.keeper.Log, []byte("first line\nthe orca app is gone\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	at := keeperStart
	w.check(t, at)
	for range 2 {
		w.starts[len(w.starts)-1].code, w.starts[len(w.starts)-1].done = 1, true
		at = at.Add(relayRespawn)
		w.check(t, at)
	}

	if len(w.starts) != 3 || len(w.posts) != 1 {
		t.Fatalf("starts = %d, posts = %+v", len(w.starts), w.posts)
	}
	told, isEscalation := w.posts[0].(bus.Escalation)
	if !isEscalation || !strings.Contains(told.Cause, "exited 1, so no desk order is read until it restarts: the orca app is gone") {
		t.Fatalf("told = %+v", w.posts[0])
	}
}

func TestStopReachesTheChildItStarted(t *testing.T) {
	w := newKeeperWorld(t)
	w.keeper.Stop()
	w.check(t, keeperStart)

	w.keeper.Stop()

	if w.starts[0].stopped != 1 {
		t.Fatalf("stopped = %d", w.starts[0].stopped)
	}
}
