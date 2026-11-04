package main

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"
)

var (
	appVersion = "dev"
	verbs      []func(*app.App) *cobra.Command
)

func register(verb func(*app.App) *cobra.Command) {
	verbs = append(verbs, verb)
}

func root(a *app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:           "lr",
		Short:         "The long-running drive's one command line",
		Version:       appVersion,
		SilenceUsage:  true,
		SilenceErrors: true,
		Args:          cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return cmd.Help()
		},
	}
	cmd.PersistentFlags().StringVar(&a.ConfigPath, "config", os.Getenv("LR_CONFIG"), "the drive's lr.json; default LR_CONFIG, for a Sprite desk with no drive registry, then the lr.json of the drive this session belongs to")
	for _, verb := range verbs {
		cmd.AddCommand(verb(a))
	}
	return cmd
}

func execute(cmd *cobra.Command, args []string) error {
	if verb, rest, err := cmd.Find(args); err == nil && verb.HasSubCommands() && !verb.Runnable() && len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		return fmt.Errorf("%s has no verb %s; see %s --help", verb.CommandPath(), rest[0], verb.CommandPath())
	}
	cmd.SetArgs(args)
	return cmd.Execute()
}

func main() {
	if err := execute(root(app.New(os.Stderr)), os.Args[1:]); err != nil {
		var exit *wait.ExitError
		if errors.As(err, &exit) {
			os.Exit(exit.Code)
		}
		fmt.Fprintln(os.Stderr, "lr:", err)
		os.Exit(1)
	}
}
