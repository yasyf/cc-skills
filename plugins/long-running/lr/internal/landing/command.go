package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
)

const (
	deskRunner = "desk-runner"
	onSprite   = "/.sprite"
	legacyHead = "head"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "landing", Short: "Move ready heads into the queue, moved heads out, and conflicts onto their base"}
	cmd.AddCommand(passCommand(a), restackCommand(a), goCommand(a))
	return cmd
}

func restackCommand(a *app.App) *cobra.Command {
	var runner, lane, sprite string
	cmd := &cobra.Command{
		Use:   "restack PR",
		Short: "Restack a PR and every open PR below it on a Sprite, so no rebase runs on this machine",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			number, err := strconv.Atoi(strings.TrimPrefix(args[0], "#"))
			if err != nil {
				return err
			}
			remote, err := NewRemote(a, runner, lane, sprite)
			if err != nil {
				return err
			}
			stdout, err := remote.Run(cmd.Context(), number)
			fmt.Fprint(cmd.OutOrStdout(), stdout)
			return err
		},
	}
	cmd.Flags().StringVar(&runner, "runner", "", "the drive's runner.json, naming the repository, the clone path and the Sprites")
	cmd.Flags().StringVar(&lane, "lane", "", "the lane each moved PR's head record comes from")
	cmd.Flags().StringVar(&sprite, "sprite", "", "the Sprite to run on, instead of the first one the runner names")
	for _, name := range []string{"runner", "lane"} {
		if err := cmd.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
	return cmd
}

func NewRemote(a *app.App, runnerPath, lane, sprite string) (Remote, error) {
	runner, err := config.LoadRunner(runnerPath)
	if err != nil {
		return Remote{}, err
	}
	var repository struct {
		Repo     string `json:"repo"`
		Checkout string `json:"checkout"`
	}
	if err := json.Unmarshal(runner.Sections["landing"], &repository); err != nil {
		return Remote{}, fmt.Errorf("%s: landing: %w", runnerPath, err)
	}
	remote := Remote{
		Runner: a.Runner, Repo: repository.Repo, Clone: repository.Checkout, Sprite: sprite,
		PostHead: func(ctx context.Context, number int, text string) error {
			_, err := client.Post(ctx, client.Draft{Drive: runner.Drive, Lane: lane, Kind: legacyHead, Text: text, To: []string{deskRunner}, PRs: []string{strconv.Itoa(number)}})
			return err
		},
	}
	if sprite != "" {
		return remote, nil
	}
	if _, err := os.Stat(onSprite); err == nil {
		remote.Here, err = os.Hostname()
		return remote, err
	}
	remote.Sprite, err = firstSprite(runner.Sections)
	return remote, err
}

func firstSprite(sections map[string]json.RawMessage) (string, error) {
	for _, desk := range []string{"fidelity", "finder"} {
		var named struct {
			Sprite json.RawMessage `json:"sprite"`
		}
		if raw, found := sections[desk]; found {
			if err := json.Unmarshal(raw, &named); err != nil {
				return "", fmt.Errorf("%s: %w", desk, err)
			}
		}
		if len(named.Sprite) == 0 {
			continue
		}
		var one string
		if json.Unmarshal(named.Sprite, &one) == nil && one != "" {
			return one, nil
		}
		var many []string
		if err := json.Unmarshal(named.Sprite, &many); err != nil {
			return "", fmt.Errorf("%s.sprite: %w", desk, err)
		}
		if len(many) > 0 {
			return many[0], nil
		}
	}
	return "", errors.New("the runner's fidelity and finder desks name no Sprite")
}
