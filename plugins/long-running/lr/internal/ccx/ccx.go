package ccx

import (
	"context"
	"encoding/json"
	"os"
	"strconv"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	MaxAgeFresh  = "0s"
	MaxAgeCached = "5m"
)

var Caller string

type Check struct {
	State string `json:"state"`
}

type PRStatus struct {
	Number       int    `json:"number"`
	Head         string `json:"head"`
	Base         string `json:"base"`
	State        string `json:"state"`
	Queue        string `json:"queue"`
	Mergeability string `json:"mergeability"`
	Downstack    int    `json:"downstack"`
	Verdict      string `json:"verdict"`
	CI           Check  `json:"ci"`
	Approval     Check  `json:"approval"`
	Unread       string `json:"unread"`
}

type PRState struct {
	Repo     string                     `json:"repo"`
	PolledAt string                     `json:"polledAt"`
	Trunk    string                     `json:"trunk"`
	Lanes    map[string]json.RawMessage `json:"lanes"`
	PRs      map[string]json.RawMessage `json:"prs"`
}

type StackBranch struct {
	Branch       string `json:"branch"`
	Path         string `json:"path"`
	Current      bool   `json:"current"`
	NeedsRestack bool   `json:"needs_restack"`
	NeedsSubmit  bool   `json:"needs_submit"`
	State        string `json:"state"`
}

type Stack struct {
	Root     string        `json:"root"`
	Branches []StackBranch `json:"branches"`
}

type Client interface {
	PRStatus(ctx context.Context, repo string, numbers []int) ([]PRStatus, error)
	PRState(ctx context.Context, repo, maxAge string, numbers []int, lanePrefixes []string) (PRState, error)
	GH(ctx context.Context, maxAge string, args ...string) (string, error)
	StackList(ctx context.Context, dir string) (Stack, error)
}

type CLI struct {
	Runner shell.Runner
	Dir    string
}

func (c CLI) PRStatus(ctx context.Context, repo string, numbers []int) ([]PRStatus, error) {
	if len(numbers) == 0 {
		return nil, nil
	}
	var rows []PRStatus
	err := c.decode(ctx, c.Dir, &rows, append([]string{"vcs", "pr", "status", "--repo", repo, "--json"}, words(numbers)...)...)
	return rows, err
}

func (c CLI) PRState(ctx context.Context, repo, maxAge string, numbers []int, lanePrefixes []string) (PRState, error) {
	if len(numbers) == 0 && len(lanePrefixes) == 0 {
		return PRState{Repo: repo}, nil
	}
	args := append([]string{"vcs", "pr", "state", "--repo", repo, "--max-age", maxAge}, words(numbers)...)
	for _, prefix := range lanePrefixes {
		args = append(args, "--lane-prefix", prefix)
	}
	var state PRState
	err := c.decode(ctx, c.Dir, &state, args...)
	return state, err
}

func (c CLI) GH(ctx context.Context, maxAge string, args ...string) (string, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: append([]string{"ccx", "vcs", "gh", "--max-age", maxAge, "--"}, args...), Env: callerEnv()})
	return result.Stdout, err
}

func (c CLI) StackList(ctx context.Context, dir string) (Stack, error) {
	var stack Stack
	err := c.decode(ctx, dir, &stack, "vcs", "stack", "list", "--json")
	return stack, err
}

func (c CLI) decode(ctx context.Context, dir string, into any, args ...string) error {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: append([]string{"ccx"}, args...), Dir: dir, Env: callerEnv()})
	if err != nil {
		return err
	}
	return json.Unmarshal([]byte(result.Stdout), into)
}

func callerEnv() []string {
	if Caller == "" || os.Getenv("CCX_CALLER") != "" {
		return nil
	}
	return []string{"CCX_CALLER=" + Caller}
}

func words(numbers []int) []string {
	out := make([]string, len(numbers))
	for i, number := range numbers {
		out[i] = strconv.Itoa(number)
	}
	return out
}
