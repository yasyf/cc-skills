package landing

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	deskRunner = "desk-runner"
	onSprite   = "/.sprite"
	legacyHead = "head"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "landing", Short: "Move ready heads into the queue, moved heads out, and conflicts onto their base"}
	cmd.AddCommand(passCommand(a), restackCommand(a), goCommand(a), gateCommand(a), orderCommand(a), clobbersCommand(a))
	return cmd
}

func clobbersCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "clobbers SQUASH...",
		Short: "Read each trunk squash against the PRs landed since its PR's base, and post a CLOBBERED record for one that removed what they added",
		Args:  cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			s, err := store.Open(filepath.Join(filepath.Dir(a.ConfigPath), storeFile))
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			clobbers := Clobbers{Runner: a.Runner, Store: s, Drive: cfg.Drive, Repo: cfg.Repo, Trunk: cfg.Trunk, Checkout: cfg.Checkout, Now: a.Now, Log: a.Log}
			for _, squash := range args {
				fmt.Fprintf(cmd.OutOrStdout(), "%s undid %d PRs\n", squash, len(clobbers.replay(cmd.Context(), squash)))
			}
			return nil
		},
	}
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
			path, err := restackRunner(runner, a)
			if err != nil {
				return err
			}
			remote, err := NewRemote(a, path, lane, sprite)
			if err != nil {
				return err
			}
			stdout, err := remote.Run(cmd.Context(), number)
			fmt.Fprint(cmd.OutOrStdout(), stdout)
			return err
		},
	}
	cmd.Flags().StringVar(&runner, "runner", "", "the drive's runner.json, naming the repository, the clone path and the Sprites; default the runner.json beside --config")
	cmd.Flags().StringVar(&lane, "lane", "", "the lane each moved PR's head record comes from")
	cmd.Flags().StringVar(&sprite, "sprite", "", "the Sprite to run on, instead of the first one the runner names")
	if err := cmd.MarkFlagRequired("lane"); err != nil {
		panic(err)
	}
	return cmd
}

func restackRunner(runner string, a *app.App) (string, error) {
	if runner != "" {
		return runner, nil
	}
	file, err := a.ConfigFile()
	if err != nil {
		return "", fmt.Errorf("lr landing restack: pass --runner or --config: %w", err)
	}
	return filepath.Join(filepath.Dir(file), runnerFile), nil
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
