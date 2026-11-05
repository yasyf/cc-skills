package wait

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
)

const (
	ReleaseRepo     = "yasyf/cc-skills"
	releaseWorkflow = "long-running lr release"
	releaseBranch   = "main"
	releaseRuns     = "30"
	releasePoll     = 45 * time.Second
	releaseTimeout  = 3600.0
	CodeQuiet       = 2
)

type releaseRun struct {
	Status     string `json:"status"`
	Conclusion string `json:"conclusion"`
	HeadSha    string `json:"headSha"`
	ID         int64  `json:"databaseId"`
	Number     int    `json:"number"`
	Attempt    int    `json:"attempt"`
}

type ReleaseWait struct {
	Commit  string
	Timeout time.Duration
	CCX     ccx.Client
	Out     io.Writer
	Warn    io.Writer
	Now     func() time.Time
	Sleep   func(context.Context, time.Duration) error
}

func releaseCommand(a *app.App) *cobra.Command {
	var commit string
	var timeout float64
	cmd := &cobra.Command{
		Use:   "release --carrying SHA",
		Short: "Block until the first successful lr release whose commit contains SHA finishes",
		Long: `Exit 0 when a successful run of the lr release workflow contains SHA, naming its tag, since a newer merge cancels a queued release and a later one carries its commits instead. A cancelled run prints nothing and a failed one prints one line, then the wait goes on. It polls GitHub in this process and prints only those lines, so a background run or Monitor wakes nobody on a tick.

Exit 1 refuses a SHA not on main, such as a pull request's head that a squash replaced, and 2 prints QUIET at --timeout-seconds.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return ReleaseWait{
				Commit:  commit,
				Timeout: time.Duration(timeout * float64(time.Second)),
				CCX:     a.CCX,
				Out:     cmd.OutOrStdout(),
				Warn:    cmd.ErrOrStderr(),
				Now:     a.Now,
				Sleep:   sleepContext,
			}.Run(cmd.Context())
		},
	}
	cmd.Flags().StringVar(&commit, "carrying", "", "the commit on main the release must contain")
	cmd.Flags().Float64Var(&timeout, "timeout-seconds", releaseTimeout, "how long to wait before printing QUIET")
	_ = cmd.MarkFlagRequired("carrying")
	return cmd
}

func sleepContext(ctx context.Context, d time.Duration) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(d):
		return nil
	}
}

func (w ReleaseWait) Run(ctx context.Context) error {
	onMain, err := w.contains(ctx, ccx.MaxAgeFresh, w.Commit, releaseBranch)
	if err != nil {
		return fmt.Errorf("read %s against %s: %w", w.Commit, releaseBranch, err)
	}
	if !onMain {
		return fmt.Errorf("%s is not on %s, so no release ever carries it; pass the merge commit", w.Commit, releaseBranch)
	}
	deadline := w.Now().Add(w.Timeout)
	carried := map[string]bool{}
	told := map[int64]bool{}
	for {
		if w.released(ctx, carried, told) {
			return nil
		}
		if !w.Now().Before(deadline) {
			fmt.Fprintf(w.Out, "QUIET no release carries %s yet\n", short(w.Commit))
			return &ExitError{CodeQuiet}
		}
		if err := w.Sleep(ctx, releasePoll); err != nil {
			return err
		}
	}
}

func (w ReleaseWait) released(ctx context.Context, carried map[string]bool, told map[int64]bool) bool {
	runs, err := w.runs(ctx)
	if err != nil {
		fmt.Fprintf(w.Warn, "lr wait release: %v\n", err)
		return false
	}
	for _, run := range runs {
		if run.Status != "completed" || run.Conclusion == "cancelled" || told[run.ID] {
			continue
		}
		carries, known := carried[run.HeadSha]
		if !known {
			if carries, err = w.contains(ctx, ccx.MaxAgeCached, w.Commit, run.HeadSha); err != nil {
				fmt.Fprintf(w.Warn, "lr wait release: %v\n", err)
				continue
			}
			carried[run.HeadSha] = carries
		}
		if !carries {
			continue
		}
		told[run.ID] = true
		if run.Conclusion == "success" {
			fmt.Fprintf(w.Out, "lr--v0.%d.%d released at %s carries %s\n", run.Number, run.Attempt, short(run.HeadSha), short(w.Commit))
			return true
		}
		fmt.Fprintf(w.Out, "lr release %s at %s run %d carries %s; still waiting\n", run.Conclusion, short(run.HeadSha), run.ID, short(w.Commit))
	}
	return false
}

func (w ReleaseWait) runs(ctx context.Context) ([]releaseRun, error) {
	out, err := w.CCX.GH(ctx, ccx.MaxAgeFresh, "run", "list", "--repo", ReleaseRepo, "--workflow", releaseWorkflow, "--branch", releaseBranch, "--limit", releaseRuns, "--json", "status,conclusion,headSha,databaseId,number,attempt")
	if err != nil {
		return nil, err
	}
	var runs []releaseRun
	if err := json.Unmarshal([]byte(out), &runs); err != nil {
		return nil, fmt.Errorf("read release runs: %w", err)
	}
	slices.SortFunc(runs, func(a, b releaseRun) int { return cmp.Compare(a.ID, b.ID) })
	return runs, nil
}

func (w ReleaseWait) contains(ctx context.Context, maxAge, commit, head string) (bool, error) {
	out, err := w.CCX.GH(ctx, maxAge, "api", "repos/"+ReleaseRepo+"/compare/"+commit+"..."+head, "--jq", ".status")
	if err != nil {
		return false, err
	}
	status := strings.TrimSpace(out)
	return status == "ahead" || status == "identical", nil
}

func short(sha string) string {
	return sha[:min(len(sha), 8)]
}
