package lanes

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var (
	namedTerminal = regexp.MustCompile(`term_[\w-]+`)
	namedPath     = regexp.MustCompile(`/\S+`)
)

func idleCensus(cmd *cobra.Command, a *app.App) error {
	s, cfg, err := opened(a)
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	if cfg.OrcaRun == "" {
		return errors.New("the drive's lr.json names no orca_run")
	}
	_, state, err := Loaded(a)
	if err != nil {
		return err
	}
	env, err := NewEnv(a.Runner, cfg, state, a.Now)
	if err != nil {
		return err
	}
	keep, err := env.keepWanted(s)
	if err != nil {
		return err
	}
	ctx := cmd.Context()
	counts, err := env.headcount(ctx)
	if err != nil {
		return err
	}
	plan, err := env.gc(ctx, append(keep, "--dry-run")...)
	if err != nil {
		return fmt.Errorf("lr gc --dry-run: %w", err)
	}
	fmt.Fprintf(cmd.OutOrStdout(), "processes claude=%d codex=%d\norca worktrees=%d terminals=%d\n", counts.claude, counts.codex, counts.worktrees, counts.terminals)
	fmt.Fprintln(cmd.OutOrStdout(), strings.Join(planSummary(plan.Stdout), "\n"))
	return nil
}

func agentProcesses(ctx context.Context, runner shell.Runner) (map[string]int, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"ps", "-axo", "comm="}, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	counts := map[string]int{}
	for line := range strings.Lines(result.Stdout) {
		counts[filepath.Base(strings.TrimSpace(line))]++
	}
	return counts, nil
}

func orcaTotal(ctx context.Context, runner shell.Runner, noun string) (int, error) {
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"orca", noun, "list", "--limit", "1", "--json"}, Timeout: readTimeout})
	if err != nil {
		return 0, err
	}
	var listed struct {
		Result struct {
			Total int `json:"totalCount"`
		} `json:"result"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &listed); err != nil {
		return 0, fmt.Errorf("orca %s list: %w", noun, err)
	}
	return listed.Result.Total, nil
}

func keptReason(reason string) string {
	reason = namedPath.ReplaceAllString(namedTerminal.ReplaceAllString(reason, "<terminal>"), "<path>")
	for _, mark := range []string{": ", " on "} {
		reason, _, _ = strings.Cut(reason, mark)
	}
	return reason
}

func planSummary(plan string) []string {
	would, kept := map[string]int{}, map[string]int{}
	for line := range strings.Lines(plan) {
		line = strings.TrimRight(line, "\n")
		if rest, planned := strings.CutPrefix(line, "would "); planned {
			verb, _, _ := strings.Cut(rest, " ")
			would[verb]++
		} else if rest, held := strings.CutPrefix(line, "keep "); held {
			_, reason, _ := strings.Cut(rest, " ")
			kept[keptReason(reason)]++
		}
	}
	verbs := make([]string, 0, len(would))
	for _, verb := range slices.Sorted(maps.Keys(would)) {
		verbs = append(verbs, fmt.Sprintf("%s=%d", verb, would[verb]))
	}
	summary := make([]string, 0, len(kept)+1)
	summary = append(summary, strings.TrimRight("gc would "+strings.Join(verbs, " "), " "))
	reasons := slices.SortedFunc(maps.Keys(kept), func(a, b string) int {
		return cmp.Or(cmp.Compare(kept[b], kept[a]), strings.Compare(a, b))
	})
	for _, reason := range reasons {
		summary = append(summary, fmt.Sprintf("keep %d %s", kept[reason], reason))
	}
	return summary
}
