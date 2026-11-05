package supervisor

import (
	"context"
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
	if cfg.OrcaRun != "" && slices.Contains(cfg.Movers, config.MoverLanes) {
		tools = append(slices.Clone(tools), "orca")
	}
	var gone []string
	for _, tool := range tools {
		if _, err := look(tool); err != nil {
			gone = append(gone, tool+" is not on PATH")
		}
	}
	reads := []struct{ what, command string }{{"the gh sign-in", "gh auth token"}}
	if slices.Contains(cfg.Movers, config.MoverLanding) && cfg.QueueKey != "" {
		reads = append(reads, struct{ what, command string }{"the queue key", cfg.QueueKey})
	}
	for _, read := range reads {
		result, err := runner.Run(ctx, shell.Command{Argv: []string{"sh", "-c", read.command}, Timeout: keyTimeout})
		if err != nil || strings.TrimSpace(result.Stdout) == "" {
			gone = append(gone, read.what+" is unreadable: `"+read.command+"` printed nothing")
		}
	}
	return gone
}
