package main

import (
	"fmt"
	"os"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
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
	}
	cmd.PersistentFlags().StringVar(&a.ConfigPath, "config", os.Getenv("LR_CONFIG"), "the drive's lr.json")
	for _, verb := range verbs {
		cmd.AddCommand(verb(a))
	}
	return cmd
}

func main() {
	if err := root(app.New(os.Stderr)).Execute(); err != nil {
		fmt.Fprintln(os.Stderr, "lr:", err)
		os.Exit(1)
	}
}
