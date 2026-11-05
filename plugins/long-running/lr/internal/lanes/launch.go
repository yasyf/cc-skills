package lanes

import (
	"context"
	"fmt"
	"maps"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	communicator = "owner-relay"
	orcaRunner   = "desk-runner"
	orcaTopic    = "orca"
	orcaAlive    = "orca alive"
	runnerStale  = 5 * time.Minute
	standingWait = 11 * time.Minute
)

type standingSession struct {
	lane, model, effort, role string
}

var standingSessions = map[string]standingSession{
	"chores":      {"chores", "sonnet", "low", ""},
	"coordinator": {Root, "fable", "high", ""},
	"meta":        {"meta", "opus", "high", RoleMeta},
	"signal":      {"signal", "sonnet", "low", ""},
}

var subDriveCoordinator = standingSession{Root, "opus", "high", ""}

func sessionFor(name, parent string) standingSession {
	if name == "coordinator" && parent != "" {
		return subDriveCoordinator
	}
	return standingSessions[name]
}

func LaunchCommand(a *app.App) *cobra.Command {
	names := strings.Join(slices.Sorted(maps.Keys(standingSessions)), "|")
	return &cobra.Command{
		Use:   fmt.Sprintf("launch {%s} BRIEF", names),
		Short: "Launch one of the standing sessions the communicator starts a drive with, bound to its own lane",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if _, ok := standingSessions[args[0]]; !ok {
				return fmt.Errorf("unknown session %q, want one of %s", args[0], names)
			}
			cfg, state, err := Loaded(a)
			if err != nil {
				return err
			}
			env, err := NewEnv(a.Runner, cfg, state, a.Now)
			if err != nil {
				return err
			}
			out, err := env.launchStanding(cmd.Context(), sessionFor(args[0], cfg.Parent), args[1])
			_, _ = fmt.Fprint(cmd.OutOrStdout(), out)
			return err
		},
	}
}

func (e Env) LaunchCoordinator(ctx context.Context, brief string, env ...string) (string, error) {
	return e.launchStanding(ctx, sessionFor("coordinator", e.Config.Parent), brief, env...)
}

func (e Env) launchStanding(ctx context.Context, session standingSession, brief string, env ...string) (string, error) {
	path, err := filepath.Abs(brief)
	if err != nil {
		return "", err
	}
	if dir := filepath.Base(filepath.Dir(path)); dir != session.lane {
		return "", fmt.Errorf("brief %s sits in a directory named %s, and lane %s launches from <state dir>/%s/brief.md", path, dir, session.lane, session.lane)
	}
	states, err := e.Bus.Tail(ctx, client.Query{Drive: e.Config.Drive, Since: e.Now().Add(-runnerStale), Kinds: []string{"state"}})
	if err != nil {
		return "", err
	}
	argv := []string{e.tool("orca-launch.sh"), session.lane, session.model, session.effort, path}
	if session.role != "" {
		env = append(env, "LAUNCH_ROLE="+session.role)
	}
	if slices.ContainsFunc(states, runnerBeat) {
		argv = []string{e.tool("desk-launch.py"), "lane", session.lane, session.model, session.effort, path, "--drive", e.Config.Drive, "--lane", communicator}
		if session.role != "" {
			argv = append(argv, "--role", session.role)
		}
	}
	result, err := e.Runner.Run(ctx, shell.Command{Argv: argv, Env: env, Timeout: standingWait})
	return result.Stdout, err
}

func runnerBeat(r client.Record) bool {
	return r.Lane == orcaRunner && r.Topic == orcaTopic && strings.HasPrefix(r.Text, orcaAlive)
}
