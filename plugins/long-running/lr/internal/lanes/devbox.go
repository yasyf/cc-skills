package lanes

import (
	"context"
	"fmt"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	devboxKind  = "evidence"
	devboxTopic = "devbox:"
	devboxMake  = 15 * time.Minute
)

func MakeDevbox(ctx context.Context, runner shell.Runner, bus Bus, drive, lane string, flags []string) (string, error) {
	argv := append([]string{"devbox", "create", "--name", lane, "--purpose", "lane " + lane + " of drive " + drive}, flags...)
	result, err := runner.Run(ctx, shell.Command{Argv: argv, Timeout: devboxMake})
	if err != nil {
		return "", err
	}
	_, err = bus.Post(ctx, client.Draft{
		Drive: drive, Lane: lane, Kind: devboxKind, Topic: devboxTopic + lane, ID: devboxTopic + lane,
		Text: fmt.Sprintf("DEVBOX %s: lane %s holds this Namespace devbox; remove it with `devbox expire %s --force` once its work is done", lane, lane, lane),
	})
	return result.Stdout, err
}

func DevboxCommand(a *app.App) *cobra.Command {
	var drive, lane string
	cmd := &cobra.Command{Use: "devbox", Short: "Make a Namespace devbox named for its lane, Linux or Mac, and record which lane holds it"}
	cmd.PersistentFlags().StringVar(&drive, "drive", "", "the drive whose bus records the holder")
	cmd.PersistentFlags().StringVar(&lane, "lane", "", "the lane that holds the box and names it")
	for _, name := range []string{"drive", "lane"} {
		if err := cmd.MarkPersistentFlagRequired(name); err != nil {
			panic(err)
		}
	}
	cmd.AddCommand(&cobra.Command{Use: "create [-- DEVBOX_CREATE_FLAGS...]", Short: "Run devbox create with the lane's name and record the holder; pass --platform and --size, such as `-- --platform macos/arm64 --size m`", RunE: func(cmd *cobra.Command, flags []string) error {
		printed, err := MakeDevbox(cmd.Context(), a.Runner, CCI{}, drive, lane, flags)
		fmt.Fprint(cmd.OutOrStdout(), printed)
		return err
	}})
	return cmd
}
