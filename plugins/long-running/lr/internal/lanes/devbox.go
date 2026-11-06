package lanes

import (
	"context"
	"fmt"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	devboxKind  = "evidence"
	devboxTopic = "devbox:"
	devboxMake  = 15 * time.Minute
)

func MakeDevbox(ctx context.Context, runner shell.Runner, bus Bus, drive, lane string, onSprite bool, flags []string) (string, error) {
	argv := append([]string{"devbox", "create", "--name", lane, "--purpose", "lane " + lane + " of drive " + drive}, flags...)
	result, err := runner.Run(ctx, shell.Command{Argv: argv, Timeout: devboxMake})
	if err != nil && onSprite {
		return "", fmt.Errorf("a Sprite has no Namespace login, so relaunch lane %s on the Mac with `place=local why=devbox` on its brief's ccx line instead of running devbox login here: %w", lane, err)
	}
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
	var driveName, lane string
	cmd := &cobra.Command{Use: "devbox", Short: "Make a Namespace devbox named for its lane, Linux or Mac, and record which lane holds it"}
	cmd.PersistentFlags().StringVar(&driveName, "drive", "", "the drive whose bus records the holder")
	cmd.PersistentFlags().StringVar(&lane, "lane", "", "the lane that holds the box and names it")
	for _, name := range []string{"drive", "lane"} {
		if err := cmd.MarkPersistentFlagRequired(name); err != nil {
			panic(err)
		}
	}
	cmd.AddCommand(&cobra.Command{Use: "create [-- DEVBOX_CREATE_FLAGS...]", Short: "Run devbox create with the lane's name and record the holder; pass --platform and --size, such as `-- --platform macos/arm64 --size m`", RunE: func(cmd *cobra.Command, flags []string) error {
		_, onSprite := drive.SpriteEntry()
		printed, err := MakeDevbox(cmd.Context(), a.Runner, CCI{}, driveName, lane, onSprite, flags)
		fmt.Fprint(cmd.OutOrStdout(), printed)
		return err
	}})
	return cmd
}
