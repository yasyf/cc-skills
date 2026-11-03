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
	return &cobra.Command{
		Use:   "gate PR...",
		Short: "Print each gate a hand enqueue of these PRs would pass over, posting nothing, and exit 1 when it prints any",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runGate(cmd.Context(), a, args, cmd.OutOrStdout())
		},
	}
}

func runGate(ctx context.Context, a *app.App, args []string, stdout io.Writer) error {
	cfg, err := a.Config()
	if err != nil {
		return err
	}
	s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), storeFile))
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	snapshot, err := facts.New(a, cfg, s).Snapshot(ctx)
	if err != nil {
		return err
	}
	byNumber := make(map[string]machine.Facts, len(snapshot))
	for _, f := range snapshot {
		byNumber[strconv.Itoa(f.Number)] = f
	}
	failing := false
	for _, pr := range args {
		f, open := byNumber[pr]
		if !open {
			failing = true
			fmt.Fprintf(stdout, "#%s is no open PR of this drive\n", pr)
			continue
		}
		for _, line := range GateLines(f) {
			failing = true
			fmt.Fprintln(stdout, line)
		}
	}
	if failing {
		return &wait.ExitError{Code: 1}
	}
	return nil
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
