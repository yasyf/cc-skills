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
	"regexp"
	"slices"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	passTimeout = 300 * time.Second
	readTimeout = 60 * time.Second
	treeLock    = ".tree.lock"
)

const QueueWorktree = "queue-worktree"

var blockedVerdict = regexp.MustCompile(`^#\d+ BLOCKED `)

type Trunk struct {
	Runner     shell.Runner
	Checkout   string
	Worktree   string
	Refs       string
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

func (t Trunk) States(ctx context.Context, numbers []int) (map[int]facts.QueueEntry, error) {
	return facts.ReadQueue(ctx, t.Runner, t.Checkout, t.KeyCommand, numbers)
}

func (t Trunk) read(ctx context.Context, args ...string) (string, error) {
	return facts.TrunkRead(ctx, t.Runner, t.Checkout, t.KeyCommand, args...)
}

func (t Trunk) call(ctx context.Context, args ...string) (string, error) {
	lock, err := LockTree(t.Worktree)
	if err != nil {
		return "", err
	}
	defer lock.Close()
	tip := t.Refs + "queue/" + t.Branch
	shallow, err := t.Runner.Run(ctx, shell.Command{Argv: []string{"git", "-C", t.Checkout, "rev-parse", "--is-shallow-repository"}, Timeout: gitLimit})
	if err != nil {
		return "", err
	}
	fetch := []string{"git", "-C", t.Checkout, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--refmap="}
	timeout := readTimeout
	if strings.TrimSpace(shallow.Stdout) == "true" {
		fetch, timeout = append(fetch, "--unshallow"), passTimeout
	}
	if _, err := t.Runner.Run(ctx, shell.Command{Argv: append(fetch, "origin", "+refs/heads/"+t.Branch+":"+tip), Timeout: timeout}); err != nil {
		return "", err
	}
	place := []string{"git", "-C", t.Worktree, "checkout", "-q", "--force", "--detach", tip}
	if _, err := os.Stat(t.Worktree); errors.Is(err, fs.ErrNotExist) {
		place = []string{"git", "-C", t.Checkout, "worktree", "add", "-q", "--force", "--detach", t.Worktree, tip}
	}
	if _, err := t.Runner.Run(ctx, shell.Command{Argv: place, Env: []string{"GIT_LFS_SKIP_SMUDGE=1"}, Timeout: passTimeout}); err != nil {
		return "", err
	}
	env, err := facts.TrunkEnv(ctx, t.Runner, t.KeyCommand)
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
	return strings.TrimSpace(fmt.Sprintf("exit %d: %s", done.Code, blockedThenLast(done))), nil
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

func blockedThenLast(done shell.Result) string {
	output := done.Stdout
	if output == "" {
		output = done.Stderr
	}
	lines := strings.Split(strings.TrimSpace(output), "\n")
	last := len(lines) - 1
	said := slices.DeleteFunc(slices.Clone(lines[:last]), func(line string) bool { return !blockedVerdict.MatchString(line) })
	return strings.Join(append(said, lines[last]), "\n")
}
