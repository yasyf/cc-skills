package shelltest

import (
	"context"
	"fmt"
	"slices"
	"sync"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

type reply struct {
	prefix []string
	result shell.Result
}

type Fake struct {
	mu      sync.Mutex
	replies []reply
	calls   []shell.Command
}

func (f *Fake) Reply(stdout string, prefix ...string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.replies = append(f.replies, reply{prefix, shell.Result{Stdout: stdout}})
}

func (f *Fake) Fail(code int, stderr string, prefix ...string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.replies = append(f.replies, reply{prefix, shell.Result{Code: code, Stderr: stderr}})
}

func (f *Fake) FailOut(code int, stdout string, prefix ...string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.replies = append(f.replies, reply{prefix, shell.Result{Code: code, Stdout: stdout}})
}

func (f *Fake) Calls() []shell.Command {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.calls)
}

func (f *Fake) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, c)
	for _, scripted := range f.replies {
		if len(c.Argv) < len(scripted.prefix) || !slices.Equal(c.Argv[:len(scripted.prefix)], scripted.prefix) {
			continue
		}
		if scripted.result.Code != 0 {
			return scripted.result, &shell.ExitError{Argv: c.Argv, Code: scripted.result.Code, Stderr: scripted.result.Stderr}
		}
		return scripted.result, nil
	}
	return shell.Result{Code: shell.CodeMissingBinary}, fmt.Errorf("no reply scripted for %q", c.Argv)
}
