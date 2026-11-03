package subdrive

import (
	"fmt"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/supervisor"
)

func Command(a *app.App) *cobra.Command {
	sub := &cobra.Command{Use: "sub", Short: "Sub-drives: a big, plainly unrelated project with its own small coordinator, reporting only to this drive's"}
	sub.AddCommand(startCommand(a), listCommand(a), endCommand(a))
	cmd := &cobra.Command{Use: "drive", Short: "This drive's relations to other drives"}
	cmd.AddCommand(sub)
	return cmd
}

func manager(a *app.App, cmd *cobra.Command) (Manager, error) {
	cfg, state, err := lanes.Loaded(a)
	if err != nil {
		return Manager{}, err
	}
	env, err := lanes.NewEnv(a.Runner, cfg, state, a.Now)
	if err != nil {
		return Manager{}, err
	}
	on := func(verb func(*cobra.Command, *app.App) error) func(string) error {
		return func(config string) error {
			child := *a
			child.ConfigPath = config
			return verb(cmd, &child)
		}
	}
	return Manager{Env: env, Supervise: on(supervisor.Install), Unsupervise: on(supervisor.Uninstall)}, nil
}

func startCommand(a *app.App) *cobra.Command {
	var request Request
	cmd := &cobra.Command{
		Use:   "start NAME",
		Short: "Start a sub-drive: its own cci drive, Orca Run, ledger, supervisor and coordinator. Never the default",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			request.Name = args[0]
			request.Facts = map[string]string{}
			for _, f := range facts {
				value, err := cmd.Flags().GetString(f.flag)
				if err != nil {
					return err
				}
				request.Facts[f.flag] = value
			}
			m, err := manager(a, cmd)
			if err != nil {
				return err
			}
			out, err := m.Start(cmd.Context(), request)
			fmt.Fprint(cmd.OutOrStdout(), out)
			return err
		},
	}
	flags := cmd.Flags()
	flags.StringVar(&request.Objective, "objective", "", "what the project delivers")
	flags.StringVar(&request.Reason, "reason", "", "why it is a project of its own and not a lane of this drive")
	flags.StringVar(&request.Repo, "repo", "", "the project's repository, default this drive's")
	flags.StringVar(&request.Checkout, "checkout", "", "the project's checkout, default this drive's")
	for _, f := range facts {
		flags.String(f.flag, "", f.usage)
	}
	return cmd
}

func listCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "Each open sub-drive: its project, coordinator, open items and why it is separate",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			m, err := manager(a, cmd)
			if err != nil {
				return err
			}
			lines, err := m.List(cmd.Context())
			if err != nil {
				return err
			}
			if len(lines) == 0 {
				lines = []string{"no open sub-drives"}
			}
			fmt.Fprintln(cmd.OutOrStdout(), strings.Join(lines, "\n"))
			return nil
		},
	}
}

func endCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "end NAME",
		Short: "End a sub-drive: its open lanes, records and PRs come back to this drive's coordinator",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			m, err := manager(a, cmd)
			if err != nil {
				return err
			}
			out, err := m.End(cmd.Context(), args[0])
			if err != nil {
				return err
			}
			fmt.Fprintln(cmd.OutOrStdout(), out)
			return nil
		},
	}
}
