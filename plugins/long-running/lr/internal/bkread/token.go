package bkread

import (
	"context"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var (
	ErrRefused = errors.New("auth failure: Buildkite refused the read token landing.ci_token_command printed")
	refused401 = regexp.MustCompile(`failed: 401\b`)
)

type Token struct {
	Command []string
	value   string
}

func (t *Token) Run(ctx context.Context, runner shell.Runner, read shell.Command) (shell.Result, error) {
	value, err := t.Value(ctx, runner)
	if err != nil {
		return shell.Result{}, err
	}
	read.Env = append(slices.Clone(read.Env), "BUILDKITE_TOKEN="+value)
	read.Idempotent = true
	result, err := runner.Run(ctx, read)
	if err != nil && refused401.MatchString(err.Error()) {
		t.value = ""
		return result, fmt.Errorf("%w: %w", ErrRefused, err)
	}
	return result, err
}

func (t *Token) Value(ctx context.Context, runner shell.Runner) (string, error) {
	if t.value == "" {
		value, err := t.print(ctx, runner)
		if err != nil {
			return "", err
		}
		t.value = value
	}
	return t.value, nil
}

func (t *Token) print(ctx context.Context, runner shell.Runner) (string, error) {
	if len(t.Command) == 0 {
		return "", errors.New("reading Buildkite needs landing.ci_token_command, the command that prints this host's read-only Buildkite token")
	}
	printed, err := runner.Run(ctx, shell.Command{Argv: t.Command, Timeout: time.Minute})
	if err != nil {
		return "", fmt.Errorf("landing.ci_token_command failed: %w", err)
	}
	value := strings.TrimSpace(printed.Stdout)
	if value == "" {
		return "", errors.New("landing.ci_token_command printed nothing")
	}
	return value, nil
}
