package supervisor

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestStatusNamesWhySupervisorHasNotReexecuted(t *testing.T) {
	for _, c := range []struct {
		name, running, pinned, served, installed string
		dead, ticking                            bool
		gcFor                                    time.Duration
		want                                     string
	}{
		{name: "the process is gone", running: "", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", dead: true, want: "not running: pid 30070 is gone"},
		{name: "no current link", running: "0.684.1", pinned: "0.691.1", served: "0.7.1", installed: "", want: "long-running/current resolves to nothing"},
		{name: "pin moved and the pack did not pass the served one", running: "0.684.1", pinned: "0.691.1", served: "0.7.2", installed: "0.7.2", want: "0.7.2 has not passed 0.7.2"},
		{name: "already on the pin", running: "0.691.1", pinned: "0.691.1", served: "0.7.2", installed: "0.7.2", want: "nothing to re-execute"},
		{name: "on the pin with a newer pack", running: "0.691.1", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", want: "nothing to re-execute"},
		{name: "pin moved with a newer pack", running: "0.684.1", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", want: "re-execute owed"},
		{name: "a pack with no pin", running: "0.684.1", pinned: "", served: "0.7.1", installed: "0.7.2", want: "re-execute owed"},
		{name: "a tick holds it", running: "0.684.1", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", ticking: true, want: "held by the tick in flight, 12s since the lease renewed; it re-executes when that tick ends"},
		{name: "a gc pass holds it", running: "0.684.1", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", gcFor: 3 * time.Minute, want: "held by an lr gc pass running 3m0s"},
		{name: "both hold it", running: "0.684.1", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", ticking: true, gcFor: 3 * time.Minute, want: "the tick in flight, 12s since the lease renewed, and an lr gc pass running 3m0s"},
		{name: "nothing holds it", running: "0.684.1", pinned: "0.691.1", served: "0.7.1", installed: "0.7.2", want: "nothing holds it"},
	} {
		t.Run(c.name, func(t *testing.T) {
			var out bytes.Buffer
			writeStatus(&out, statusFacts{
				Lease: store.Lease{PID: 30070, Generation: 7, Version: c.served}, Alive: !c.dead, Running: c.running, Pinned: c.pinned, Installed: c.installed, Age: 12 * time.Second, Ticking: c.ticking, GCFor: c.gcFor,
			})
			if !strings.Contains(out.String(), c.want) {
				t.Fatalf("status lacks %q:\n%s", c.want, out.String())
			}
		})
	}
}

func TestStatusReadsATickAsHoldingOnlyWhileItRuns(t *testing.T) {
	s, c := memory(t), &clock{t0}
	during := false
	read := func() bool {
		lease, err := s.Lease("d")
		if err != nil {
			t.Fatal(err)
		}
		ticking, _, err := holding(s, lease, c.now)
		if err != nil {
			t.Fatal(err)
		}
		return ticking
	}
	d := Deps{Movers: []string{"lanes"}, Clock: c.read, Sleep: c.sleep, Log: slog.New(slog.NewTextHandler(io.Discard, nil)), Lanes: func(time.Time, int64) (lanes.Outcome, error) {
		during = read()
		return lanes.Outcome{}, nil
	}}

	runLoop(t, s, "0.8.0", 1, d, 1)

	equal(t, during, true)
	equal(t, read(), false)
}

func TestRunningBinaryReadsTheProcessNotThePath(t *testing.T) {
	linux := &shelltest.Fake{}
	linux.Reply("lr version 0.684.1\n", "/proc/30070/exe", "--version")
	if got, err := runningBinary(context.Background(), linux, "linux", 30070); err != nil || got != "0.684.1" {
		t.Fatal(got, err)
	}

	mac := &shelltest.Fake{}
	mac.Reply("p30070\nftxt\nn/cache/lr-0.690.1/lr\nftxt\nn/usr/lib/dyld\n", "lsof", "-a", "-p", "30070")
	mac.Reply("lr version 0.690.1\n", "/cache/lr-0.690.1/lr", "--version")
	if got, err := runningBinary(context.Background(), mac, "darwin", 30070); err != nil || got != "0.690.1" {
		t.Fatal(got, err)
	}
}
