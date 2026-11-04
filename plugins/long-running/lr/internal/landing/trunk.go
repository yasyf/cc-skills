package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	passTimeout = 300 * time.Second
	readTimeout = 60 * time.Second
	queueKeyEnv = "TRUNK_MERGE_API_KEY"
)

type Trunk struct {
	Runner     shell.Runner
	Checkout   string
	Branch     string
	Script     string
	KeyCommand string
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

func (t Trunk) LastFailure(ctx context.Context, number int) string {
	env, err := t.env(ctx)
	if err != nil {
		return ""
	}
	done, err := t.Runner.Run(ctx, shell.Command{
		Argv:    []string{filepath.Join(t.Checkout, "tools/ci"), "trunk", "history", strconv.Itoa(number)},
		Dir:     t.Checkout,
		Env:     env,
		Timeout: readTimeout,
	})
	if err != nil {
		return ""
	}
	var history []struct {
		State  string `json:"state"`
		Reason string `json:"reason"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &history); err != nil {
		return ""
	}
	for _, entry := range slices.Backward(history) {
		if entry.State == "failed" {
			return entry.Reason
		}
	}
	return ""
}

func (t Trunk) call(ctx context.Context, args ...string) (string, error) {
	for _, argv := range [][]string{
		{"git", "-C", t.Checkout, "fetch", "-q", "origin", t.Branch},
		{"git", "-C", t.Checkout, "checkout", "-q", "--detach", "FETCH_HEAD"},
	} {
		if _, err := t.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: readTimeout}); err != nil {
			return "", err
		}
	}
	env, err := t.env(ctx)
	if err != nil {
		return "", err
	}
	done, err := t.Runner.Run(ctx, shell.Command{
		Argv:    append([]string{filepath.Join(t.Checkout, t.Script)}, args...),
		Dir:     t.Checkout,
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
