package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	passTimeout = 300 * time.Second
	readTimeout = 60 * time.Second
	queueKeyEnv = "TRUNK_MERGE_API_KEY"
	treeLock    = ".tree.lock"
	queueTip    = "refs/lr/queue/"
)

const QueueWorktree = "queue-worktree"

type Trunk struct {
	Runner     shell.Runner
	Checkout   string
	Worktree   string
	Branch     string
	Script     string
	KeyCommand string
	Logs       func(ctx context.Context, build string) (job, test, infra string)
}

type queueCheck struct {
	Name string `json:"name"`
	URL  string `json:"url"`
}

type queueTransition struct {
	State     string `json:"state"`
	ChangedAt string `json:"changedAt"`
	Reason    string `json:"reason"`
	Run       string `json:"verificationRunId"`
	Failed    struct {
		Checks []queueCheck `json:"failureCausingChecks"`
	} `json:"failed"`
	PendingFailure struct {
		Checks []queueCheck `json:"failureCausingChecks"`
	} `json:"pendingFailure"`
}

func (t Trunk) Enqueue(ctx context.Context, number int, _ string) (string, error) {
	return t.call(ctx, strconv.Itoa(number))
}

func (t Trunk) Reenter(ctx context.Context, number int) (string, error) {
	return t.call(ctx, strconv.Itoa(number), "--flake")
}

func (t Trunk) Dequeue(ctx context.Context, number int) (string, error) {
	return t.call(ctx, strconv.Itoa(number), "--dequeue")
}

func (t Trunk) LastFailure(ctx context.Context, number int, since time.Time) (Failure, error) {
	out, err := t.read(ctx, "history", strconv.Itoa(number))
	if err != nil {
		return Failure{}, err
	}
	var history []queueTransition
	if err := json.Unmarshal([]byte(out), &history); err != nil {
		return Failure{}, fmt.Errorf("#%d queue history: %w", number, err)
	}
	failure := failureOf(history)
	if failure.State == "" {
		return Failure{}, nil
	}
	ended, err := time.Parse(time.RFC3339, failure.At)
	if err != nil {
		return Failure{}, fmt.Errorf("#%d queue history at %q: %w", number, failure.At, err)
	}
	if ended.Before(since) {
		return Failure{}, nil
	}
	if failure.Run != "" {
		if failure.MatesFailed, err = t.matesFailed(ctx, number, failure.Run); err != nil {
			return failure, err
		}
	}
	if t.Logs != nil && failure.State == entryFailed && failure.URL != "" {
		failure.Job, failure.Test, failure.Infra = t.Logs(ctx, failure.URL)
	}
	return failure, nil
}

func failureOf(history []queueTransition) Failure {
	for at := len(history) - 1; at >= 0; at-- {
		entry := history[at]
		if entry.State != entryFailed && entry.State != entryCancelled {
			continue
		}
		failure := Failure{State: entry.State, At: entry.ChangedAt, Reason: entry.Reason, Run: entry.Run}
		checks := entry.Failed.Checks
		for attempt := at - 1; attempt >= 0 && history[attempt].State != entryNotReady; attempt-- {
			if failure.Run == "" {
				failure.Run = history[attempt].Run
			}
			if len(checks) == 0 {
				checks = history[attempt].PendingFailure.Checks
			}
		}
		if len(checks) > 0 {
			failure.Check, failure.URL = checks[0].Name, checks[0].URL
		}
		return failure
	}
	return Failure{}
}

func (t Trunk) matesFailed(ctx context.Context, number int, run string) ([]int, error) {
	out, err := t.read(ctx, "verification", strconv.Itoa(number), "--run", run)
	if err != nil {
		return nil, err
	}
	var verification struct {
		Tested []struct {
			Number int `json:"number"`
		} `json:"testedPullRequests"`
	}
	if err := json.Unmarshal([]byte(out), &verification); err != nil {
		return nil, fmt.Errorf("#%d verification %s: %w", number, run, err)
	}
	var mates []int
	for _, tested := range verification.Tested {
		if tested.Number != number {
			mates = append(mates, tested.Number)
		}
	}
	if len(mates) == 0 {
		return nil, nil
	}
	states, err := t.States(ctx, mates)
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(mates, func(mate int) bool { return !slices.Contains(failedEntries, states[mate].State) }), nil
}

func (t Trunk) States(ctx context.Context, numbers []int) (map[int]QueueEntry, error) {
	args := make([]string, 0, 1+len(numbers))
	args = append(args, "status")
	for _, number := range numbers {
		args = append(args, strconv.Itoa(number))
	}
	out, err := t.read(ctx, args...)
	if err != nil {
		return nil, err
	}
	var read map[string]struct {
		SHA   string `json:"sha"`
		Entry *struct {
			State     string          `json:"state"`
			ChangedAt string          `json:"stateChangedAt"`
			Readiness json.RawMessage `json:"readiness"`
		} `json:"queueEntry"`
	}
	if err := json.Unmarshal([]byte(out), &read); err != nil {
		return nil, fmt.Errorf("queue status: %w", err)
	}
	states := make(map[int]QueueEntry, len(read))
	for number, pull := range read {
		if pull.Entry == nil {
			continue
		}
		at, err := strconv.Atoi(number)
		if err != nil {
			return nil, fmt.Errorf("queue status names %q: %w", number, err)
		}
		states[at] = QueueEntry{State: pull.Entry.State, Head: pull.SHA, ChangedAt: pull.Entry.ChangedAt, Readiness: string(pull.Entry.Readiness)}
	}
	return states, nil
}

func (t Trunk) read(ctx context.Context, args ...string) (string, error) {
	env, err := t.env(ctx)
	if err != nil {
		return "", err
	}
	done, err := t.Runner.Run(ctx, shell.Command{
		Argv:    append([]string{filepath.Join(t.Checkout, "tools/ci"), "trunk"}, args...),
		Dir:     t.Checkout,
		Env:     env,
		Timeout: readTimeout,
	})
	return done.Stdout, err
}

func (t Trunk) call(ctx context.Context, args ...string) (string, error) {
	lock, err := LockTree(t.Worktree)
	if err != nil {
		return "", err
	}
	defer lock.Close()
	tip := queueTip + t.Branch
	if _, err := t.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", t.Checkout, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--refmap=", "origin", "+refs/heads/" + t.Branch + ":" + tip}, Timeout: readTimeout}); err != nil {
		return "", err
	}
	place := []string{"git", "-C", t.Worktree, "checkout", "-q", "--force", "--detach", tip}
	if _, err := os.Stat(t.Worktree); errors.Is(err, fs.ErrNotExist) {
		place = []string{"git", "-C", t.Checkout, "worktree", "add", "-q", "--force", "--detach", t.Worktree, tip}
	}
	if _, err := t.Runner.Run(ctx, shell.Command{Argv: place, Env: []string{"GIT_LFS_SKIP_SMUDGE=1"}, Timeout: passTimeout}); err != nil {
		return "", err
	}
	env, err := t.env(ctx)
	if err != nil {
		return "", err
	}
	done, err := t.Runner.Run(ctx, shell.Command{
		Argv:    append([]string{filepath.Join(t.Worktree, t.Script)}, args...),
		Dir:     t.Worktree,
		Env:     env,
		Timeout: passTimeout,
	})
	var exit *shell.ExitError
	switch {
	case done.Code == shell.CodeTimedOut:
		return fmt.Sprintf("timed out after %ds", int(passTimeout.Seconds())), nil
	case err != nil && !errors.As(err, &exit):
		return "", err
	}
	return strings.TrimSpace(fmt.Sprintf("exit %d: %s", done.Code, lastLine(done))), nil
}

func LockTree(checkout string) (io.Closer, error) {
	lock, err := os.OpenFile(checkout+treeLock, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		lock.Close()
		return nil, err
	}
	return lock, nil
}

func (t Trunk) env(ctx context.Context) ([]string, error) {
	if t.KeyCommand == "" {
		return nil, nil
	}
	key, err := t.Runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", t.KeyCommand}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	return []string{queueKeyEnv + "=" + strings.TrimSpace(key.Stdout)}, nil
}

func lastLine(done shell.Result) string {
	output := done.Stdout
	if output == "" {
		output = done.Stderr
	}
	lines := strings.Split(strings.TrimSpace(output), "\n")
	return lines[len(lines)-1]
}
