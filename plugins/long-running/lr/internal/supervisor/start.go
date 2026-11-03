package supervisor

import (
	"context"
	"maps"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const keyTimeout = 30 * time.Second

var startTools = []string{"git", "gh", "cci", "ccx"}

func missing(ctx context.Context, runner shell.Runner, look func(string) (string, error), cfg config.Config) []string {
	tools := startTools
	if cfg.OrcaRun != "" {
		tools = append(slices.Clone(tools), "orca")
	}
	var gone []string
	for _, tool := range tools {
		if _, err := look(tool); err != nil {
			gone = append(gone, tool+" is not on PATH")
		}
	}
	keys := map[string]string{"the GitHub token": "gh auth token"}
	if slices.Contains(cfg.Movers, config.MoverLanding) && cfg.QueueKey != "" {
		keys["the queue key"] = cfg.QueueKey
	}
	for _, key := range slices.Sorted(maps.Keys(keys)) {
		result, err := runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", keys[key]}, Timeout: keyTimeout})
		if err != nil || strings.TrimSpace(result.Stdout) == "" {
			gone = append(gone, key+" is unreadable: `"+keys[key]+"` printed nothing")
		}
	}
	return gone
}
