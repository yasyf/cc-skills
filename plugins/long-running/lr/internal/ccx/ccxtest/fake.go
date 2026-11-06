package ccxtest

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"sync"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
)

type Fake struct {
	Statuses []ccx.PRStatus
	State    ccx.PRState
	Stack    ccx.Stack
	GHOut    map[string]string
	Failed   map[int64][]string

	mu    sync.Mutex
	calls []string
}

func (f *Fake) Calls() []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.calls)
}

func (f *Fake) record(call string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, call)
}

func (f *Fake) PRStatus(_ context.Context, repo string, numbers []int) ([]ccx.PRStatus, error) {
	f.record(fmt.Sprintf("pr status %s %v", repo, numbers))
	var rows []ccx.PRStatus
	for _, row := range f.Statuses {
		if slices.Contains(numbers, row.Number) {
			rows = append(rows, row)
		}
	}
	return rows, nil
}

func (f *Fake) PRState(_ context.Context, repo, maxAge string, numbers []int, lanePrefixes []string) (ccx.PRState, error) {
	f.record(fmt.Sprintf("pr state %s %s %v %v", repo, maxAge, numbers, lanePrefixes))
	return f.State, nil
}

func (f *Fake) GH(_ context.Context, maxAge string, args ...string) (string, error) {
	joined := strings.Join(args, " ")
	f.record(fmt.Sprintf("gh %s %s", maxAge, joined))
	out, found := f.GHOut[joined]
	if !found {
		return "", fmt.Errorf("no gh output scripted for %q", joined)
	}
	return out, nil
}

func (f *Fake) StackList(_ context.Context, dir string) (ccx.Stack, error) {
	f.record("stack list " + dir)
	return f.Stack, nil
}

func (f *Fake) FailedTests(_ context.Context, repo string, job int64) ([]string, error) {
	f.record(fmt.Sprintf("job-log %s %d --failed-tests", repo, job))
	return f.Failed[job], nil
}

var _ ccx.Client = (*Fake)(nil)
