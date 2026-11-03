package shell

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	CodeTimedOut      = 124
	CodeMissingBinary = 127
	killGrace         = 5 * time.Second
)

type Command struct {
	Argv    []string
	Stdin   string
	Dir     string
	Env     []string
	Timeout time.Duration
}

type Result struct {
	Code   int
	Stdout string
	Stderr string
}

type Runner interface {
	Run(ctx context.Context, c Command) (Result, error)
}

type ExitError struct {
	Argv   []string
	Code   int
	Stderr string
}

func (e *ExitError) Error() string {
	return fmt.Sprintf("%s exited %d: %s", strings.Join(e.Argv, " "), e.Code, strings.TrimSpace(e.Stderr))
}

type Exec struct{}

func (Exec) Run(ctx context.Context, c Command) (Result, error) {
	if c.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.Timeout)
		defer cancel()
	}
	var stdout, stderr bytes.Buffer
	cmd := exec.CommandContext(ctx, c.Argv[0], c.Argv[1:]...)
	cmd.Dir = c.Dir
	cmd.Stdin = strings.NewReader(c.Stdin)
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	cmd.WaitDelay = killGrace
	if len(c.Env) > 0 {
		cmd.Env = append(os.Environ(), c.Env...)
	}
	err := cmd.Run()
	result := Result{Stdout: stdout.String(), Stderr: stderr.String()}
	var exit *exec.ExitError
	switch {
	case err == nil:
		return result, nil
	case errors.Is(err, exec.ErrNotFound):
		result.Code = CodeMissingBinary
		return result, fmt.Errorf("%s is not on PATH: %w", c.Argv[0], err)
	case ctx.Err() != nil:
		result.Code = CodeTimedOut
		return result, fmt.Errorf("%s: %w", strings.Join(c.Argv, " "), ctx.Err())
	case errors.As(err, &exit):
		result.Code = exit.ExitCode()
		return result, &ExitError{Argv: c.Argv, Code: result.Code, Stderr: result.Stderr}
	default:
		return result, err
	}
}
