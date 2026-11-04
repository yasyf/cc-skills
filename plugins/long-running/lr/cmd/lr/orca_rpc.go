package main

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orcarpc"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const parityBudget = 2 * time.Minute

func init() {
	register(orcaCommand)
}

func orcaCommand(*app.App) *cobra.Command {
	cmd := &cobra.Command{
		Use:                "orca [args]",
		Short:              "Run an orca command, answering read verbs without starting Orca's CLI",
		Long:               "Takes the same arguments as orca. A read verb with --json is answered over Orca's local socket with the CLI's own output. Everything else, and anything the socket can't answer, runs the real orca.",
		DisableFlagParsing: true,
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args) == 1 && args[0] == "parity" {
				ctx, cancel := context.WithTimeout(cmd.Context(), parityBudget)
				defer cancel()
				return orcarpc.RecordParity(ctx, orcarpc.New(), shell.Exec{})
			}
			return runOrca(cmd.Context(), args)
		},
	}
	return cmd
}

func runOrca(ctx context.Context, args []string) error {
	client := orcarpc.New()
	client.Gate = &orcarpc.Gate{Spawn: orcarpc.SpawnParity}
	served, ok := client.Serve(ctx, args)
	if ok {
		if _, err := os.Stdout.Write(served.Stdout); err != nil {
			return err
		}
		if served.Code != 0 {
			os.Exit(served.Code)
		}
		return nil
	}
	path, err := exec.LookPath("orca")
	if err != nil {
		return fmt.Errorf("orca is not on PATH: %w", err)
	}
	return orcarpc.Passthrough(path, append(args, served.RetryFlags()...))
}
