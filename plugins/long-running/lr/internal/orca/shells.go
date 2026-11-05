package orca

import (
	"cmp"
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	shellQuiet = 3 * 24 * time.Hour
	deviceRoot = "/dev"
	unboundRun = "run_unbound"
	runEvery   = 5 * time.Minute

	skipRunsConfig = "lr.gcSkipRun"
	gitConfigUnset = 1
)

func (p Processes) EmptyShells(ctx context.Context) (map[string]bool, error) {
	childless, err := p.idleLoginShells(ctx)
	if err != nil {
		return nil, err
	}
	out, err := p.stdout(ctx, "ps", "-axo", "pid=,tty=")
	if err != nil {
		return nil, err
	}
	empty := map[string]bool{}
	for line := range strings.Lines(out) {
		row := strings.Fields(line)
		if len(row) != 2 || !childless[row[0]] {
			continue
		}
		device, err := os.Stat(filepath.Join(cmp.Or(p.Dev, deviceRoot), row[1]))
		if err == nil && time.Since(device.ModTime()) > shellQuiet {
			empty[row[0]] = true
		}
	}
	return empty, nil
}

type Run struct {
	ID          string `json:"id"`
	Coordinator string `json:"coordinator_handle"`
	Legacy      int    `json:"legacy"`
}

func skippedRuns(ctx context.Context, runner shell.Runner) (map[string]bool, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "config", "--global", "--get-all", skipRunsConfig}})
	var exit *shell.ExitError
	if errors.As(err, &exit) && exit.Code == gitConfigUnset {
		return map[string]bool{}, nil
	}
	if err != nil {
		return nil, err
	}
	skipped := map[string]bool{}
	for _, run := range strings.Fields(result.Stdout) {
		skipped[run] = true
	}
	return skipped, nil
}

func (g *GC) OrphanedRun(ctx context.Context, current string) (Tally, error) {
	if !g.DryRun && g.Pause.Held() {
		return Tally{}, nil
	}
	runs, err := g.Orca.Runs(ctx)
	if err != nil {
		return nil, err
	}
	terminals, err := g.Orca.Terminals(ctx)
	if err != nil {
		return nil, err
	}
	open := map[string]bool{}
	for _, terminal := range terminals {
		open[terminal.Handle] = true
	}
	orphaned := slices.DeleteFunc(runs, func(run Run) bool {
		return run.ID == current || run.ID == unboundRun || run.Legacy != 0 || open[run.Coordinator] || g.SkipRuns[run.ID]
	})
	if len(orphaned) == 0 {
		return Tally{}, nil
	}
	slices.SortFunc(orphaned, func(a, b Run) int { return strings.Compare(a.ID, b.ID) })
	pass := int(g.Orca.NowMS() / runEvery.Milliseconds())
	run := orphaned[pass%len(orphaned)]
	workers, err := g.Orca.Workers(ctx, run.ID)
	if err != nil {
		return nil, err
	}
	settled := make([]string, 0, len(workers))
	for _, worker := range workers {
		if worker.Settled() && open[worker.Terminal] && !g.Keep[worker.Dispatch] {
			settled = append(settled, worker.Dispatch)
		}
	}
	if len(settled) == 0 {
		return Tally{}, nil
	}
	first := pass % len(settled)
	batch := slices.Concat(settled[first:], settled[:first])
	return g.Run(ctx, run.ID, batch[:min(len(batch), sweptBatch)], nil)
}

func emptyShellPID(terminal Terminal, sessions Sessions) string {
	bearers := sessions[terminal.Handle]
	if terminal.Agent != "" || terminal.Worktree == "" || !terminal.Connected || len(bearers) != 1 {
		return ""
	}
	return bearers[0].PID
}

func (g *GC) Shells(ctx context.Context) (Tally, error) {
	tally := Tally{}
	if !g.DryRun && g.Pause.Held() {
		return tally, nil
	}
	terminals, err := g.Orca.Terminals(ctx)
	if err != nil {
		return nil, err
	}
	nowMS := g.Orca.NowMS()
	if !slices.ContainsFunc(terminals, func(terminal Terminal) bool {
		return terminal.Agent == "" && terminal.Worktree != "" && nowMS-terminal.LastOutputMS > shellQuiet.Milliseconds()
	}) {
		return tally, nil
	}
	sessions, err := g.Orca.Sessions(ctx)
	if err != nil {
		return nil, err
	}
	empty, err := g.Orca.EmptyShells(ctx)
	if err != nil {
		return nil, err
	}
	agents := map[string]bool{}
	for _, terminal := range terminals {
		if terminal.Agent != "" || len(sessions[terminal.Handle]) > 1 {
			agents[terminal.Worktree] = true
		}
	}
	ended := 0
	for _, terminal := range terminals {
		pid := emptyShellPID(terminal, sessions)
		if ended == sweptBatch || pid == "" || !empty[pid] || agents[terminal.Worktree] {
			continue
		}
		action := Action{Verb: verbEnd, Target: terminal.Handle, Reason: fmt.Sprintf("empty shell, quiet three days, in %s", terminal.Worktree)}
		if !g.DryRun {
			again, err := g.Orca.EmptyShells(ctx)
			if err != nil {
				return nil, err
			}
			if !again[pid] {
				tally[g.report(Action{Verb: verbKeep, Target: terminal.Handle, Reason: changed}).Verb]++
				continue
			}
		}
		done, err := g.act(ctx, action)
		if err != nil {
			return nil, err
		}
		tally[done.Verb]++
		ended++
	}
	return tally, nil
}
