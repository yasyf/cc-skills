package grader

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	stageWords  = 4
	failureTail = 300
)

var (
	shims     = map[string]string{"ccx": "cc-context"}
	shellSafe = regexp.MustCompile(`^[\w@%+=:,./-]+$`)
)

type ExitError struct {
	Argv   []string
	Code   int
	Stdout string
	Stderr string
}

func (e *ExitError) Error() string {
	said := e.Stderr
	if said == "" {
		said = e.Stdout
	}
	return fmt.Sprintf("%s exited %d: %s", shellJoin(e.Argv[:min(len(e.Argv), stageWords)]), e.Code, lastRunes(strings.Join(strings.Fields(said), " "), failureTail))
}

func (e *ExitError) output() string {
	return e.Stdout + e.Stderr
}

type TimeoutError struct {
	Argv  []string
	After time.Duration
}

func (e *TimeoutError) Error() string {
	return fmt.Sprintf("TimeoutExpired: %s timed out after %s", shellJoin(e.Argv[:min(len(e.Argv), stageWords)]), e.After)
}

func exited(err error) bool {
	var exit *ExitError
	return errors.As(err, &exit)
}

func shellJoin(argv []string) string {
	quoted := make([]string, 0, len(argv))
	for _, word := range argv {
		if shellSafe.MatchString(word) {
			quoted = append(quoted, word)
			continue
		}
		quoted = append(quoted, "'"+strings.ReplaceAll(word, "'", `'"'"'`)+"'")
	}
	return strings.Join(quoted, " ")
}

func lastRunes(text string, count int) string {
	runes := []rune(text)
	return string(runes[max(len(runes)-count, 0):])
}

func (p *pass) run(argv []string, dir string, timeout time.Duration) (string, error) {
	return p.execute(shell.Command{Argv: argv, Dir: dir, Timeout: timeout})
}

func (p *pass) execute(command shell.Command) (string, error) {
	started := time.Now()
	argv, timeout := command.Argv, command.Timeout
	command.Argv = p.shimmed(argv)
	result, err := p.Runner.Run(p.ctx, command)
	p.Log.Info("command", "stage", stage(argv), "seconds", time.Since(started).Seconds())
	if err == nil {
		return result.Stdout, nil
	}
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return "", &ExitError{argv, result.Code, result.Stdout, result.Stderr}
	}
	if result.Code == shell.CodeTimedOut {
		return "", &TimeoutError{argv, timeout}
	}
	return "", err
}

func (p *pass) shimmed(argv []string) []string {
	plugin, known := shims[argv[0]]
	if !known {
		return argv
	}
	shim := filepath.Join(p.Config, "plugins", "marketplaces", plugin, "plugin", "bin", argv[0])
	if _, err := os.Stat(shim); err != nil {
		return argv
	}
	return append([]string{shim}, argv[1:]...)
}

func stage(argv []string) string {
	words := make([]string, 0, stageWords)
	for _, word := range argv[:min(len(argv), stageWords)] {
		if strings.Contains(word, "/") {
			word = filepath.Base(word)
		}
		words = append(words, word)
	}
	return strings.Join(words, " ")
}
