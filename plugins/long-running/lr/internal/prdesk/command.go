package prdesk

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading/grader"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var pacific = func() *time.Location {
	zone, err := time.LoadLocation("America/Los_Angeles")
	if err != nil {
		panic(err)
	}
	return zone
}()

const (
	deskFile   = "pr-desk.sqlite"
	boardLane  = "board-watch"
	queueCheck = "Trunk Merge Queue"
	leaseEvery = 30 * time.Second
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "pr", Short: "The PR desk, the one owner of every PR's state"}
	cmd.AddCommand(serveCommand(a), statusCommand(a), whyCommand(a))
	return cmd
}

func statusCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Print every PR's one state, mover, deadline and reason, asked of the running PR desk",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var rows []store.PRState
			if err := ask(cmd.Context(), socketPath(a), "/status", &rows); err != nil {
				return err
			}
			for _, row := range rows {
				fmt.Fprintf(cmd.OutOrStdout(), "#%d %s %s%s: %s\n", row.Number, row.State, row.Mover, due(row.Deadline), row.Reason)
			}
			return nil
		},
	}
}

func whyCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "why <pr>",
		Short: "Say why one PR is where it is: its state and reason, its open holds, its carried taps and what the desk owes it",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			var why Why
			if err := ask(cmd.Context(), socketPath(a), "/why/"+strings.TrimPrefix(args[0], "#"), &why); err != nil {
				return err
			}
			out := cmd.OutOrStdout()
			row := why.State
			fmt.Fprintf(out, "#%d at %s is %s, moved by %s%s: %s\n", row.Number, row.Head, row.State, row.Mover, due(row.Deadline), row.Reason)
			for _, h := range why.Holds {
				fmt.Fprintf(out, "held by %s until %s: %s\n", h.Lifter, h.Until, h.Reason)
			}
			for _, c := range why.Carries {
				fmt.Fprintf(out, "tap carried to %s from #%d at %s by %s\n", c.ToHead, c.FromNumber, c.FromHead, c.Proof)
			}
			for _, o := range why.Owed {
				fmt.Fprintf(out, "owed: %s at %s\n", o.Action, o.Head)
			}
			return nil
		},
	}
}

func socketPath(a *app.App) string {
	return filepath.Join(filepath.Dir(a.ConfigPath), socketFile)
}

func due(deadline time.Time) string {
	if deadline.IsZero() {
		return ""
	}
	return ", due " + deadline.In(pacific).Format("Jan 2 3:04 PM")
}

func serveCommand(a *app.App) *cobra.Command {
	var shadow bool
	var to []string
	var origin string
	cmd := &cobra.Command{
		Use:   "serve",
		Short: "Run the PR desk over live events; with --shadow it posts each disagreement with the runner and acts on nothing",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if !shadow {
				return errors.New("the PR desk acts on nothing until landing switches to it: run it with --shadow")
			}
			return serve(cmd.Context(), a, to, origin)
		},
	}
	cmd.Flags().BoolVar(&shadow, "shadow", false, "follow live events into pr-desk.sqlite and post each disagreement with the runner, acting on nothing")
	cmd.Flags().StringSliceVar(&to, "to", nil, "the lanes its disagreements go to")
	cmd.Flags().StringVar(&origin, "board-origin", "", "the cci node the owner's board posts taps from; a tap from any other node counts for nothing")
	_ = cmd.MarkFlagRequired("to")
	_ = cmd.MarkFlagRequired("board-origin")
	return cmd
}

func serve(ctx context.Context, a *app.App, to []string, origin string) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), deskFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	held := Lane + "@" + cfg.Drive
	lease, err := s.AcquireLease(held, "lr", os.Getpid(), a.Now())
	if err != nil {
		return err
	}
	required, err := requiredChecks(ctx, a.CCX, cfg.Repo, cfg.Trunk)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithCancelCause(ctx)
	defer cancel(nil)
	go func() {
		tick := time.NewTicker(leaseEvery)
		defer tick.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-tick.C:
				if err := s.RenewLease(held, lease.Generation, a.Now()); err != nil {
					cancel(err)
					return
				}
			}
		}
	}()
	listener, err := listen(socketPath(a))
	if err != nil {
		return err
	}
	status := statusServer(s, cfg.Repo)
	go func() {
		if err := status.Serve(listener); !errors.Is(err, http.ErrServerClosed) {
			cancel(err)
		}
	}()
	defer func() { _ = status.Close() }()
	desk := Desk{s, map[string]Repo{cfg.Repo: {Name: cfg.Repo, Landing: cfg.Trunk, Required: required}}}
	err = Shadow{
		Desk: desk, Repo: cfg.Repo, Drive: cfg.Drive, To: to, Board: boardLane, Origin: origin,
		Graders: []string{grader.FinderLane, grader.FidelityLane}, GoLanes: cfg.GoLaneNames(),
		Read: GitHubRead{CCX: a.CCX, Store: s, Repo: cfg.Repo, Author: cfg.Author, Label: cfg.Label}.Read,
		Tail: client.Tail, Post: client.Post, Now: a.Now, Log: a.Log,
		Trunk: TrunkRead{Runner: a.Runner, KeyCommand: cfg.QueueKey, URL: TrunkAPI, HTTP: &http.Client{Timeout: time.Minute}, Repo: cfg.Repo, Landing: cfg.Trunk}.Read,
		Hash: func(ctx context.Context, base, head string) (string, error) {
			return hashDiff(ctx, a.Runner, cfg.Checkout, base, head)
		},
	}.Run(ctx)
	if cause := context.Cause(ctx); cause != nil && !errors.Is(cause, context.Canceled) {
		return cause
	}
	return err
}

func requiredChecks(ctx context.Context, gh ccx.Client, repo, landing string) ([]string, error) {
	out, err := gh.GH(ctx, ccx.MaxAgeFresh, "api", "repos/"+repo+"/rules/branches/"+landing)
	if err != nil {
		return nil, err
	}
	var rules []struct {
		Type       string `json:"type"`
		Parameters struct {
			Checks []struct {
				Context string `json:"context"`
			} `json:"required_status_checks"`
		} `json:"parameters"`
	}
	if err := json.Unmarshal([]byte(out), &rules); err != nil {
		return nil, err
	}
	var required []string
	for _, rule := range rules {
		if rule.Type != "required_status_checks" {
			continue
		}
		for _, check := range rule.Parameters.Checks {
			if !strings.HasPrefix(check.Context, queueCheck) {
				required = append(required, check.Context)
			}
		}
	}
	return required, nil
}

func hashDiff(ctx context.Context, runner shell.Runner, checkout, base, head string) (string, error) {
	tracked := "refs/pr-desk/" + base
	if _, err := runner.Run(ctx, shell.Command{Dir: checkout, Argv: []string{"git", "fetch", "--no-write-fetch-head", "-q", "origin", head, "+refs/heads/" + base + ":" + tracked}}); err != nil {
		return "", err
	}
	diff, err := runner.Run(ctx, shell.Command{Dir: checkout, Argv: []string{"git", "diff", "--no-color", "--no-ext-diff", tracked + "..." + head}})
	if err != nil {
		return "", err
	}
	return gates.DiffHash(diff.Stdout), nil
}
