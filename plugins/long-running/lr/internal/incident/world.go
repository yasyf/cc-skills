package incident

import (
	"context"
	"errors"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var ErrResponseLost = errors.New("response lost")

type EffectFailed struct {
	Reason string
}

func (e *EffectFailed) Error() string {
	return e.Reason
}

func isLost(err error) bool {
	var exit *shell.ExitError
	return errors.As(err, &exit) || errors.Is(err, context.DeadlineExceeded) || errors.Is(err, ErrResponseLost)
}

type LaunchReceipt struct {
	Lane     string `json:"lane"`
	State    string `json:"state"`
	Task     string `json:"task"`
	Dispatch string `json:"dispatch"`
	Terminal string `json:"terminal,omitempty"`
	Worktree string `json:"worktree,omitempty"`
}

type PRView struct {
	State string
	Head  string
	URL   string
}

type BuildPR struct {
	ID string `json:"id"`
}

type BuildSource struct {
	Number int `json:"number"`
}

type Job struct {
	ID      string `json:"id"`
	Name    string `json:"name"`
	StepKey string `json:"step_key"`
	State   string `json:"state"`
}

type Build struct {
	Number      int          `json:"number"`
	Commit      string       `json:"commit"`
	State       string       `json:"state"`
	WebURL      string       `json:"web_url"`
	PullRequest *BuildPR     `json:"pull_request"`
	RebuiltFrom *BuildSource `json:"rebuilt_from"`
	Jobs        []Job        `json:"jobs"`
}

type Orca interface {
	Launch(ctx context.Context, lane, model, effort, brief string) (LaunchReceipt, error)
	Receipt(lane string) (*LaunchReceipt, error)
	Engaged(ctx context.Context, lane string, now time.Time) (bool, error)
}

type GitHub interface {
	PRs(ctx context.Context, numbers []int) (map[int]PRView, error)
	Landed(ctx context.Context, pr int) (string, error)
}

type Buildkite interface {
	Configuration(ctx context.Context) (string, error)
	Failed(ctx context.Context, since, until string) ([]Build, error)
	Unfinished(ctx context.Context, since, until string) ([]Build, error)
	Since(ctx context.Context, since string) ([]Build, error)
	Rebuild(ctx context.Context, number int) (Build, error)
}

type Activation interface {
	Prepare(ctx context.Context, sha string) (string, error)
	Drifted(ctx context.Context, tree string) (bool, error)
	Apply(ctx context.Context, tree string) ([]string, error)
}

type Comms interface {
	Event(ctx context.Context, event string, facts map[string]any, grant string) (int64, error)
	Find(ctx context.Context, event string) (int64, error)
	Posted(ctx context.Context, seq int64) (string, error)
	AskRoot(ctx context.Context, text string) error
	Report(ctx context.Context, text string) error
	Fence(ctx context.Context, text string) error
}

type Records interface {
	Open(ctx context.Context, id, title, premise string) (RecordIDs, error)
	Entry(ctx context.Context, log, text, attach string) error
	Verdict(ctx context.Context, investigation, verb, text, commit string) error
}

type World struct {
	Orca       Orca
	GitHub     GitHub
	Buildkite  Buildkite
	Activation Activation
	Comms      Comms
	Records    Records
}
