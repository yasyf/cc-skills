package landing

import (
	"context"
	"fmt"
	"io"
	"path/filepath"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const tapRequired = "TAP-REQUIRED no approval on the owner's board at this head"

func gateCommand(a *app.App) *cobra.Command {
	var partial bool
	cmd := &cobra.Command{
		Use:   "gate PR...",
		Short: "Print each gate a hand enqueue of these PRs would pass over, posting nothing, and exit 1 when it prints any",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runGate(cmd.Context(), a, args, partial, cmd.OutOrStdout())
		},
	}
	cmd.Flags().BoolVar(&partial, "partial", false, "The enqueue takes only the named PRs and those below them, not the stacks above")
	return cmd
}

func runGate(ctx context.Context, a *app.App, args []string, partial bool, stdout io.Writer) error {
	lines, err := gateLines(ctx, a, args, partial)
	if err != nil {
		fmt.Fprintf(a.Stderr, "Warning, nothing was refused: lr landing gate could not read the drive's pull requests: %v\n", err)
		return nil
	}
	for _, line := range lines {
		fmt.Fprintln(stdout, line)
	}
	if len(lines) > 0 {
		return &wait.ExitError{Code: 1}
	}
	return nil
}

func gateLines(ctx context.Context, a *app.App, args []string, partial bool) ([]string, error) {
	cfg, err := a.Config()
	if err != nil {
		return nil, err
	}
	dir := filepath.Dir(a.ConfigPath)
	s, err := store.Open(filepath.Join(dir, storeFile))
	if err != nil {
		return nil, err
	}
	defer func() { _ = s.Close() }()
	snapshot, err := facts.New(a, cfg, s).Snapshot(ctx)
	if err != nil {
		return nil, err
	}
	g, err := NewGates(a, s, cfg, filepath.Join(dir, runnerFile))
	if err != nil {
		return nil, err
	}
	return g.HandEnqueueLines(ctx, snapshot, args, partial), nil
}

func (g *Gates) HandEnqueueLines(ctx context.Context, snapshot []machine.Facts, named []string, partial bool) []string {
	byNumber := make(map[string]machine.Facts, len(snapshot))
	byBranch := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byNumber[strconv.Itoa(f.Number)] = f
		byBranch[f.Branch] = f
	}
	var lines []string
	below := map[string]bool{}
	for _, pr := range named {
		f, open := byNumber[pr]
		if !open {
			lines = append(lines, fmt.Sprintf("#%s is no open PR of this drive", pr))
		}
		for ; open; f, open = byBranch[f.Base] {
			below[f.Branch] = true
		}
	}
	for _, f := range snapshot {
		if below[f.Branch] || !partial && downstackMeets(f, byBranch, below) {
			lines = append(append(lines, GateLines(f)...), g.refusalLines(ctx, f)...)
		}
	}
	return lines
}

func (g *Gates) refusalLines(ctx context.Context, f machine.Facts) []string {
	kept, err := g.Store.Facts(f.Repo, f.Number, f.Head)
	if err != nil {
		g.Log.Warn("the hand enqueue gate did not read its facts", "pr", f.Number, "err", err)
		return nil
	}
	var lines []string
	if kept[rulingsClear] == "" {
		rows, _, err := g.reservedRows(ctx, f)
		if err != nil {
			g.Log.Warn("the rulings gate did not read, so it passes this head this pass", "pr", f.Number, "err", err)
		}
		for _, row := range rows {
			lines = append(lines, fmt.Sprintf("#%d %s RESERVED %s: %s", f.Number, shortHead(f.Head), row.Key, row.Sentence))
		}
	}
	if g.Build.Tree == "" || kept[buildClear] != "" {
		return lines
	}
	failure, _, err := g.readBuildFailure(ctx, f)
	if err != nil {
		g.Log.Warn("the build gate did not read, so it passes this head this pass", "pr", f.Number, "err", err)
		return lines
	}
	if failure != "" {
		lines = append(lines, fmt.Sprintf("#%d %s BUILD-FAILED %s", f.Number, shortHead(f.Head), failure))
	}
	return lines
}

func GateLines(f machine.Facts) []string {
	missing := gates.Missing(f)
	lines := make([]string, 0, len(missing))
	for _, gate := range missing {
		if gate == gates.NoTap {
			gate = tapRequired
		}
		lines = append(lines, fmt.Sprintf("#%d %s %s", f.Number, shortHead(f.Head), gate))
	}
	return lines
}
