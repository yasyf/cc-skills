package awake

import (
	"bytes"
	"context"
	"errors"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type box struct {
	cancel    func()
	probes    []string
	refreshes []error
	puts      [][]string
	deletes   [][]string
}

func (b *box) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if slices.Contains(c.Argv, "DELETE") {
		b.deletes = append(b.deletes, c.Argv)
		return shell.Result{}, nil
	}
	if ctx.Err() != nil {
		return shell.Result{Stdout: "none\n"}, nil
	}
	if c.Argv[0] == "sh" {
		if len(b.probes) == 0 {
			b.cancel()
			return shell.Result{Stdout: "none\n"}, nil
		}
		answer := b.probes[0]
		b.probes = b.probes[1:]
		if answer == "" {
			return shell.Result{}, errors.New("the probe failed")
		}
		return shell.Result{Stdout: answer + "\n"}, nil
	}
	b.puts = append(b.puts, c.Argv)
	if len(b.refreshes) == 0 {
		return shell.Result{}, nil
	}
	answer := b.refreshes[0]
	b.refreshes = b.refreshes[1:]
	if len(b.refreshes) == 0 && b.probes == nil {
		b.cancel()
	}
	return shell.Result{}, answer
}

func held(t *testing.T, b *box, wanted func(shell.Runner) func(context.Context) (bool, error)) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()
	b.cancel = cancel
	var log bytes.Buffer
	Holder{Runner: b, Task: "lane", Every: time.Millisecond, Log: slog.New(slog.NewTextHandler(&log, nil)), Wanted: wanted(b)}.Run(ctx)
	if errors.Is(ctx.Err(), context.DeadlineExceeded) {
		t.Fatal("the holder never reached the end of its script")
	}
	return log.String()
}

func always(shell.Runner) func(context.Context) (bool, error) {
	return Always
}

func TestAWorkerHoldRefreshesOnlyWhileAnAgentRunsOnTheBox(t *testing.T) {
	b := &box{probes: []string{"running", "running", "none", "none", "running"}}
	if log := held(t, b, WorkerAlive); strings.Contains(log, "WARN") {
		t.Fatal(log)
	}
	if len(b.puts) != 3 {
		t.Fatalf("%d refreshes for 3 reads that found an agent", len(b.puts))
	}
}

func TestARefreshAsksForAShortExpiryOnTheNamedTask(t *testing.T) {
	b := &box{probes: []string{"running"}}
	held(t, b, WorkerAlive)
	want := []string{"/.sprite/bin/sprite-env", "curl", "-sSf", "-X", "PUT", "/v1/tasks/lane", "-H", "Content-Type: application/json", "-d", `{"expire": "5m"}`}
	if !slices.Equal(b.puts[0], want) {
		t.Fatal(b.puts[0])
	}
}

func TestAFailedRefreshWarnsAndTriesAgainAtTheNextTick(t *testing.T) {
	b := &box{refreshes: []error{errors.New("no socket"), nil}}
	log := held(t, b, always)
	if len(b.puts) != 2 {
		t.Fatalf("%d refreshes, want the failed one and its retry", len(b.puts))
	}
	if !strings.Contains(log, "level=WARN") || !strings.Contains(log, "no socket") {
		t.Fatal(log)
	}
}

func TestAFailedAgentReadWarnsHoldsNothingThatTickAndReadsAgain(t *testing.T) {
	b := &box{probes: []string{"", "running"}}
	log := held(t, b, WorkerAlive)
	if len(b.puts) != 1 {
		t.Fatalf("%d refreshes, want one from the read that found an agent", len(b.puts))
	}
	if !strings.Contains(log, "level=WARN") || !strings.Contains(log, "the probe failed") {
		t.Fatal(log)
	}
}

func TestAStoppedHolderReleasesItsTaskOnce(t *testing.T) {
	b := &box{probes: []string{"running"}}
	held(t, b, WorkerAlive)
	want := []string{"/.sprite/bin/sprite-env", "curl", "-sSf", "-X", "DELETE", "/v1/tasks/lane"}
	if len(b.deletes) != 1 || !slices.Equal(b.deletes[0], want) {
		t.Fatal(b.deletes)
	}
}
