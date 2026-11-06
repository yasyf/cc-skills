package landing

import (
	"context"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net/url"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"

	"github.com/spf13/cobra"
	"github.com/spf13/pflag"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

const (
	tapRequired   = "TAP-REQUIRED no approval on the owner's board at this head"
	queueUntested = "QUEUE-UNTESTED"
	queuePassed   = "success"
	gateUnread    = "Warning, nothing was refused:"
	gateRead      = "landing gate"
)

func gateCommand(a *app.App) *cobra.Command {
	var partial, direct bool
	cmd := &cobra.Command{
		Use:   "gate PR...",
		Short: "Print each gate a hand enqueue of these PRs would pass over, posting nothing, and exit 1 when it prints any",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			return runGate(cmd.Context(), a, args, partial, direct, cmd.OutOrStdout())
		},
	}
	gateFlags(cmd.Flags(), &partial, &direct)
	return cmd
}

func gateFlags(flags *pflag.FlagSet, partial, direct *bool) {
	flags.BoolVar(partial, "partial", false, "The enqueue takes only the named PRs and those below them, not the stacks above")
	flags.BoolVar(direct, "direct", false, "The landing merges directly, outside the queue, so each head must have passed the queue's own test")
}

func GateRead(a *app.App, driveName string) askdesk.Serve {
	return func(ctx context.Context, args []string, out, errs io.Writer) error {
		var partial, direct bool
		parsed := pflag.NewFlagSet(gateRead, pflag.ContinueOnError)
		parsed.SetOutput(io.Discard)
		gateFlags(parsed, &partial, &direct)
		if err := parsed.Parse(args); err != nil {
			return err
		}
		if parsed.NArg() == 0 {
			return errors.New(gateRead + " takes one or more PRs")
		}
		entry, err := drive.Named(driveName)
		if err != nil {
			return err
		}
		served := *a
		served.ConfigPath, served.Stderr = entry.Config(), errs
		lines, err := gateLines(ctx, &served, parsed.Args(), partial, direct)
		if err != nil {
			return err
		}
		for _, line := range lines {
			fmt.Fprintln(out, line)
		}
		return nil
	}
}

func runGate(ctx context.Context, a *app.App, args []string, partial, direct bool, stdout io.Writer) error {
	lines, err := gateLines(ctx, a, args, partial, direct)
	if errors.Is(err, drive.ErrOnMac) {
		lines, err = askGate(ctx, a, args, partial, direct)
	}
	if errors.Is(err, fs.ErrNotExist) {
		return fmt.Errorf("lr landing gate cannot run without its input: %w", err)
	}
	if err != nil {
		fmt.Fprintf(a.Stderr, "%s lr landing gate could not read the drive's pull requests: %v\n", gateUnread, err)
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

func askGate(ctx context.Context, a *app.App, args []string, partial, direct bool) ([]string, error) {
	cfg, err := a.Config()
	if err != nil {
		return nil, err
	}
	entry, err := drive.Named(cfg.Drive)
	if err != nil {
		return nil, err
	}
	ask := slices.Concat([]string{gateRead}, args)
	if partial {
		ask = append(ask, "--partial")
	}
	if direct {
		ask = append(ask, "--direct")
	}
	var answer strings.Builder
	if err := askdesk.AskMac(ctx, askdesk.LiveCCI{}, entry.Bus(), os.Getenv(lanes.LaneEnv), strings.Join(ask, " "), &answer); err != nil {
		return nil, err
	}
	return strings.FieldsFunc(answer.String(), func(r rune) bool { return r == '\n' }), nil
}

func gateLines(ctx context.Context, a *app.App, args []string, partial, direct bool) ([]string, error) {
	cfg, err := a.Config()
	if err != nil {
		return nil, err
	}
	if entry, err := drive.Named(cfg.Drive); err == nil && entry.OnSprite() {
		return nil, drive.ErrOnMac
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
	g.GoAnswer, g.Stderr = sweepAnswer(ctx, a, cfg, s), a.Stderr
	return g.HandEnqueueLines(ctx, snapshot, args, partial, direct), nil
}

func sweepAnswer(ctx context.Context, a *app.App, cfg config.Config, s *store.Store) func(context.Context, machine.Facts) ([]string, error) {
	inputs := sync.OnceValues(func() (gateInputs, error) { return readGateInputs(ctx, cfg.Drive, a.Now()) })
	return func(ctx context.Context, f machine.Facts) ([]string, error) {
		read, err := inputs()
		if err != nil {
			return nil, err
		}
		return goGate{a: a, cfg: cfg, s: s, inputs: read, stdout: a.Stderr}.answer(ctx, f)
	}
}

func (g *Gates) HandEnqueueLines(ctx context.Context, snapshot []machine.Facts, named []string, partial, direct bool) []string {
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
			lines = append(append(lines, g.tapLines(ctx, f)...), g.refusalLines(ctx, f)...)
			if direct {
				lines = append(lines, g.untestedLines(ctx, f)...)
			}
		}
	}
	return lines
}

func (g *Gates) tapLines(ctx context.Context, f machine.Facts) []string {
	missing := gates.Missing(f)
	if !goReady(f) {
		return missingLines(f, missing)
	}
	missing = slices.DeleteFunc(missing, func(gate string) bool { return gate == gates.NoTap || gate == gates.NoGo })
	refusals, err := g.GoAnswer(ctx, f)
	switch {
	case err != nil:
		fmt.Fprintf(g.Stderr, "%s lr landing gate could not read the go sweep's answer on #%d: %v\n", gateUnread, f.Number, err)
	case len(refusals) > 0:
		missing = append(missing, tapRequired+", and the go sweep refuses it: "+strings.Join(refusals, "; "))
	default:
		missing = append(missing, gates.NoGo)
	}
	return missingLines(f, missing)
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

func (g *Gates) untestedLines(ctx context.Context, f machine.Facts) []string {
	check := facts.QueueCheck(g.Trunk)
	conclusion, err := g.CCX.GH(ctx, ccx.MaxAgeFresh, "api", "repos/"+f.Repo+"/commits/"+f.Head+"/check-runs?filter=latest&check_name="+url.QueryEscape(check), "--jq", ".check_runs[0].conclusion")
	if err != nil {
		g.Log.Warn("the queue's check did not read, so the direct merge gate passes this head", "pr", f.Number, "err", err)
		return nil
	}
	if strings.TrimSpace(conclusion) == queuePassed {
		return nil
	}
	return []string{fmt.Sprintf("#%d %s %s %s has not passed at this head", f.Number, shortHead(f.Head), queueUntested, check)}
}

func GateLines(f machine.Facts) []string {
	return missingLines(f, gates.Missing(f))
}

func missingLines(f machine.Facts, missing []string) []string {
	lines := make([]string, 0, len(missing))
	for _, gate := range missing {
		if gate == gates.NoTap {
			gate = tapRequired
		}
		lines = append(lines, fmt.Sprintf("#%d %s %s", f.Number, shortHead(f.Head), gate))
	}
	return lines
}
