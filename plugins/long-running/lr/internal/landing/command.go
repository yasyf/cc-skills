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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	deskRunner      = "desk-runner"
	onSprite        = "/.sprite"
	legacyHead      = "head"
	tapStoreFile    = "lr.sqlite"
	openPullsMaxAge = "1m"
	openPullsLimit  = "1000"
	openPullsFields = "number,title,body,headRefOid,headRefName,baseRefName"
)

func Command(a *app.App) *cobra.Command {
	cmd := &cobra.Command{Use: "landing", Short: "Move ready heads into the queue, moved heads out, and conflicts onto their base"}
	cmd.AddCommand(restackCommand(a), askTapsCommand(a), drawTapsCommand(a))
	return cmd
}

func askTapsCommand(a *app.App) *cobra.Command {
	var runnerPath string
	cmd := &cobra.Command{
		Use:   "ask-taps",
		Short: "Put every PR the desk says waits on the owner's tap on his Slack card, and raise one blocker per PR and head left with none",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var ask TapAsk
			if err := json.NewDecoder(cmd.InOrStdin()).Decode(&ask); err != nil {
				return fmt.Errorf("the desk's verdict on stdin: %w", err)
			}
			tapper, closeStore, err := newTapper(a, runnerPath)
			if err != nil {
				return err
			}
			defer closeStore()
			report, err := tapper.Ask(cmd.Context(), ask, a.Now())
			if err != nil {
				return err
			}
			return json.NewEncoder(cmd.OutOrStdout()).Encode(report)
		},
	}
	cmd.Flags().StringVar(&runnerPath, "runner", "", "the drive's runner.json, naming the repository, the drive and landing.owner_thread")
	if err := cmd.MarkFlagRequired("runner"); err != nil {
		panic(err)
	}
	return cmd
}

func newTapper(a *app.App, runnerPath string) (Tapper, func(), error) {
	runner, err := config.LoadRunner(runnerPath)
	if err != nil {
		return Tapper{}, nil, err
	}
	var section struct {
		Repo        string `json:"repo"`
		OwnerThread string `json:"owner_thread"`
	}
	if err := json.Unmarshal(runner.Sections["landing"], &section); err != nil {
		return Tapper{}, nil, fmt.Errorf("%s: landing: %w", runnerPath, err)
	}
	if section.OwnerThread == "" {
		return Tapper{}, nil, fmt.Errorf("%s: landing.owner_thread must name the ts of the owner's Slack DM status thread, so a tap card has a thread to post in", runnerPath)
	}
	s, err := store.Open(filepath.Join(filepath.Dir(runnerPath), tapStoreFile))
	if err != nil {
		return Tapper{}, nil, err
	}
	pulls := func(ctx context.Context) ([]board.Pull, error) {
		out, err := a.CCX.GH(ctx, openPullsMaxAge, "pr", "list", "--repo", section.Repo, "--state", "open", "--limit", openPullsLimit, "--json", openPullsFields)
		if err != nil {
			return nil, err
		}
		var pulls []board.Pull
		err = json.Unmarshal([]byte(out), &pulls)
		return pulls, err
	}
	return Tapper{Store: s, Bus: tapInbox{runner.Drive}, Pulls: pulls, Repo: section.Repo, Thread: section.OwnerThread}, func() { _ = s.Close() }, nil
}

func drawTapsCommand(a *app.App) *cobra.Command {
	var drive, session string
	var write int64
	cmd := &cobra.Command{
		Use:   "draw-taps",
		Short: "On the board's machine, draw the tap card on stdin and record each Slack card it posted against its PR and head",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var card board.TapCard
			if err := json.NewDecoder(cmd.InOrStdin()).Decode(&card); err != nil {
				return fmt.Errorf("the tap card on stdin: %w", err)
			}
			links, said, drawErr := DrawTaps(cmd.Context(), a.Runner, session, card)
			if len(links) > 0 {
				raw, err := json.Marshal(links)
				if err != nil {
					return err
				}
				if _, err := client.Post(cmd.Context(), client.Draft{
					Drive: drive, Lane: boardWatchLane, Kind: appliedKind, To: []string{deskRunner}, ID: fmt.Sprintf("tap-cards:%d", write),
					Text:  fmt.Sprintf("board write #%d drew %d Slack-carded rows", write, len(links)),
					Attrs: map[string]string{tapCardsAttr: string(raw)},
				}); err != nil {
					return err
				}
			}
			if drawErr != nil {
				return drawErr
			}
			fmt.Fprintln(cmd.OutOrStdout(), said)
			return nil
		},
	}
	cmd.Flags().StringVar(&drive, "drive", "", "the drive the board belongs to")
	cmd.Flags().StringVar(&session, "session", "", "the board's session")
	cmd.Flags().Int64Var(&write, "write", 0, "the seq of the board-write record this draw answers")
	for _, name := range []string{"drive", "session", "write"} {
		if err := cmd.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
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
