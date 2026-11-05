package shell

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

const (
	CodeTimedOut      = 124
	CodeMissingBinary = 127
	killGrace         = 5 * time.Second
)

var (
	transient  = regexp.MustCompile(`(?i)(?:failed:|HTTP|returned error:|status[ =:])\s*(?:429|5\d\d)\b|\b(?:429 Too Many Requests|50[0-4] (?:Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout))\b|overloaded|connection reset|i/o timeout|TLS handshake timeout|secondary rate limit|remote end hung up unexpectedly|\b(?:remote_)?runtime_(?:unavailable|timeout)\b`)
	retryWaits = []time.Duration{5 * time.Second, 15 * time.Second, 45 * time.Second, 90 * time.Second}
)

type Command struct {
	Argv       []string
	Stdin      string
	Dir        string
	Env        []string
	Timeout    time.Duration
	Idempotent bool
	Stdout     io.Writer
	Stderr     io.Writer
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
	Tries  int
}

func (e *ExitError) Error() string {
	tries := ""
	if e.Tries > 1 {
		tries = fmt.Sprintf(" after %d tries", e.Tries)
	}
	return fmt.Sprintf("%s exited %d%s: %s", strings.Join(e.Argv, " "), e.Code, tries, strings.TrimSpace(e.Stderr))
}

type Exec struct{}

func (Exec) Run(ctx context.Context, c Command) (Result, error) {
	for tries := 1; ; tries++ {
		result, err := once(ctx, c)
		var exit *ExitError
		if !c.Idempotent || !errors.As(err, &exit) {
			return result, err
		}
		exit.Tries = tries
		if tries > len(retryWaits) || !transient.MatchString(exit.Stderr+"\n"+result.Stdout) {
			return result, err
		}
		select {
		case <-ctx.Done():
			return result, err
		case <-time.After(retryWaits[tries-1]):
		}
	}
}

// CommandContext is exec.CommandContext for a tool a package manager may have
// unlinked from PATH to upgrade it: a tool PATH lacks runs from the plugin that
// carries it. Nothing has started when PATH misses, so this never runs a tool twice.
func CommandContext(ctx context.Context, name string, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, name, args...)
	if errors.Is(cmd.Err, exec.ErrNotFound) {
		if carried := pluginBinary(name); carried != "" {
			cmd.Path, cmd.Err = carried, nil
		}
	}
	return cmd
}

func LookPath(tool string) (string, error) {
	found, err := exec.LookPath(tool)
	if errors.Is(err, exec.ErrNotFound) {
		if carried := pluginBinary(tool); carried != "" {
			return carried, nil
		}
	}
	return found, err
}

func pluginBinary(tool string) string {
	config, err := drive.ConfigDir()
	if err != nil {
		return ""
	}
	raw, err := os.ReadFile(filepath.Join(config, "plugins", "installed_plugins.json"))
	if err != nil {
		return ""
	}
	var installed struct {
		Plugins map[string][]struct {
			Scope       string `json:"scope"`
			InstallPath string `json:"installPath"`
		} `json:"plugins"`
	}
	if json.Unmarshal(raw, &installed) != nil {
		return ""
	}
	for _, id := range slices.Sorted(maps.Keys(installed.Plugins)) {
		for _, entry := range installed.Plugins[id] {
			carried := filepath.Join(entry.InstallPath, "bin", tool)
			if _, err := exec.LookPath(carried); entry.Scope == "user" && err == nil {
				return carried
			}
		}
	}
	return ""
}

func once(ctx context.Context, c Command) (Result, error) {
	if c.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, c.Timeout)
		defer cancel()
	}
	var stdout, stderr bytes.Buffer
	cmd := CommandContext(ctx, c.Argv[0], c.Argv[1:]...)
	cmd.Dir = c.Dir
	cmd.Stdin = strings.NewReader(c.Stdin)
	cmd.Stdout = &stdout
	if c.Stdout != nil {
		cmd.Stdout = c.Stdout
	}
	cmd.Stderr = &stderr
	if c.Stderr != nil {
		cmd.Stderr = io.MultiWriter(&stderr, c.Stderr)
	}
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
		return result, fmt.Errorf("%s is not on PATH and no installed plugin carries it, so install it: %w", c.Argv[0], err)
	case ctx.Err() != nil:
		result.Code = CodeTimedOut
		return result, fmt.Errorf("%s: %w", strings.Join(c.Argv, " "), ctx.Err())
	case errors.Is(err, exec.ErrWaitDelay):
		return result, nil
	case errors.As(err, &exit):
		result.Code = exit.ExitCode()
		return result, &ExitError{Argv: c.Argv, Code: result.Code, Stderr: result.Stderr}
	default:
		return result, err
	}
}
