package facts

import (
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	queueKeyEnv = "TRUNK_MERGE_API_KEY"
	trunkLimit  = 60 * time.Second
)

type QueueEntry struct {
	State     string
	Head      string
	ChangedAt string
	Readiness string
	Submitted bool
}

func ReadQueue(ctx context.Context, runner shell.Runner, checkout, keyCommand string, numbers []int) (map[int]QueueEntry, error) {
	args := make([]string, 0, 1+len(numbers))
	args = append(args, "status")
	for _, number := range numbers {
		args = append(args, strconv.Itoa(number))
	}
	out, err := TrunkRead(ctx, runner, checkout, keyCommand, args...)
	if err != nil {
		return nil, err
	}
	var read map[string]struct {
		SHA       string `json:"sha"`
		Submitted bool   `json:"isCurrentlySubmittedToQueue"`
		Entry     *struct {
			State     string          `json:"state"`
			ChangedAt string          `json:"stateChangedAt"`
			Readiness json.RawMessage `json:"readiness"`
		} `json:"queueEntry"`
	}
	if err := json.Unmarshal([]byte(out), &read); err != nil {
		return nil, fmt.Errorf("queue status: %w", err)
	}
	entries := make(map[int]QueueEntry, len(read))
	for number, pull := range read {
		if pull.Entry == nil && !pull.Submitted {
			continue
		}
		at, err := strconv.Atoi(number)
		if err != nil {
			return nil, fmt.Errorf("queue status names %q: %w", number, err)
		}
		entry := QueueEntry{Head: pull.SHA, Submitted: pull.Submitted}
		if pull.Entry != nil {
			entry.State, entry.ChangedAt, entry.Readiness = pull.Entry.State, pull.Entry.ChangedAt, string(pull.Entry.Readiness)
		}
		entries[at] = entry
	}
	return entries, nil
}

func TrunkRead(ctx context.Context, runner shell.Runner, checkout, keyCommand string, args ...string) (string, error) {
	env, err := TrunkEnv(ctx, runner, keyCommand)
	if err != nil {
		return "", err
	}
	done, err := runner.Run(ctx, shell.Command{
		Argv:    append([]string{filepath.Join(checkout, "tools/ci"), "trunk"}, args...),
		Dir:     checkout,
		Env:     env,
		Timeout: trunkLimit,
	})
	return done.Stdout, err
}

func TrunkEnv(ctx context.Context, runner shell.Runner, keyCommand string) ([]string, error) {
	if keyCommand == "" {
		return nil, nil
	}
	key, err := runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", keyCommand}, Timeout: trunkLimit})
	if err != nil {
		return nil, err
	}
	return []string{queueKeyEnv + "=" + strings.TrimSpace(key.Stdout)}, nil
}

func (s Source) trunkStatuses(ctx context.Context, numbers []int, members memberRead) ([]statusRow, error) {
	rows, err := s.graphStatuses(ctx, numbers, members)
	if err != nil || len(numbers) == 0 {
		return rows, err
	}
	entries, err := ReadQueue(ctx, s.Runner, s.Checkout, s.QueueKey, numbers)
	if err != nil {
		s.Log.Warn("the Trunk queue went unread, so no pull request lands this pass", "error", err)
		for at := range rows {
			rows[at].Verdict = queueUnread(rows[at].Verdict)
		}
		return rows, nil
	}
	for at, row := range rows {
		entry, found := entries[row.Number]
		if !found || entry.Head != row.Head {
			continue
		}
		rows[at].Queue, rows[at].Mergeability = "not queued", entry.State
		if entry.Submitted {
			rows[at].Queue = "queued"
		}
	}
	return rows, nil
}

func queueUnread(verdict string) string {
	if blocked, found := strings.CutPrefix(verdict, "blocked:"); found {
		return "blocked:" + blocked + "," + queueUnknown
	}
	return "blocked:" + queueUnknown
}
