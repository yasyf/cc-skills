package board

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	LinkLane   = "board-link"
	cardKind   = "board.card"
	cursorFile = "board-link.cursor"
	firstRead  = 1
	saidLimit  = 300
)

type cardBody struct {
	Session string          `json:"session"`
	Block   json.RawMessage `json:"block,omitempty"`
	Remove  string          `json:"remove,omitempty"`
}

func DrawCard(ctx context.Context, driveName, lane, key, session string, block json.RawMessage, remove string) (client.Record, error) {
	if (len(block) == 0) == (remove == "") {
		return client.Record{}, errors.New("a card change is one block to draw or one block id to remove")
	}
	body, err := json.Marshal(cardBody{Session: session, Block: block, Remove: remove})
	if err != nil {
		return client.Record{}, err
	}
	return client.Post(ctx, client.Draft{
		Drive: driveName, Lane: lane, Kind: cardKind, ID: cardKind + "/" + key, Text: "card change " + key,
		To: []string{LinkLane}, Body: body, Force: true,
	})
}

type Link struct {
	Runner shell.Runner
	Drive  string
	State  string
	From   []string
}

func (l Link) Serve(ctx context.Context) error {
	after, err := l.cursor()
	if err != nil {
		return err
	}
	return client.Follow(ctx, client.Watch{Drive: l.Drive, Reader: LinkLane, After: after}, func(record client.Record) error {
		if err := l.apply(ctx, record); err != nil {
			return err
		}
		return os.WriteFile(filepath.Join(l.State, cursorFile), []byte(strconv.FormatInt(record.Seq, 10)), 0o600)
	})
}

func (l Link) cursor() (int64, error) {
	raw, err := os.ReadFile(filepath.Join(l.State, cursorFile))
	if errors.Is(err, os.ErrNotExist) {
		return firstRead, nil
	}
	if err != nil {
		return 0, err
	}
	return strconv.ParseInt(string(raw), 10, 64)
}

func (l Link) apply(ctx context.Context, record client.Record) error {
	if string(record.Kind) != cardKind {
		return nil
	}
	if !slices.Contains(l.From, record.Lane) {
		return l.refuse(ctx, record, "this lane does not draw cards on the board")
	}
	var card cardBody
	if err := json.Unmarshal(record.Body, &card); err != nil {
		return l.refuse(ctx, record, err.Error())
	}
	_, err := l.Runner.Run(ctx, presentCommand(card))
	var refused *shell.ExitError
	if errors.As(err, &refused) {
		return l.refuse(ctx, record, strings.TrimSpace(refused.Stderr))
	}
	return err
}

func presentCommand(card cardBody) shell.Command {
	if card.Remove != "" {
		return shell.Command{Argv: []string{"cc-present", "remove-block", card.Remove, "--session", card.Session}}
	}
	return shell.Command{Argv: []string{"cc-present", "update-block", "-", "--session", card.Session}, Stdin: string(card.Block)}
}

func (l Link) refuse(ctx context.Context, record client.Record, said string) error {
	_, err := client.Post(ctx, client.Draft{
		Drive: l.Drive, Lane: LinkLane, Kind: "defect", ID: fmt.Sprintf("%s-refused/%d", cardKind, record.Seq),
		Text: fmt.Sprintf("card #%d did not reach the board: %s", record.Seq, said[:min(len(said), saidLimit)]),
		To:   []string{record.Lane}, Force: true,
	})
	return err
}

func linkLabel(driveName string) string {
	return "com.long-running." + driveName + ".board-link"
}

func linkCommand(a *app.App, opened func() (drive.Entry, string, error)) *cobra.Command {
	var from []string
	cmd := &cobra.Command{Use: "link", Short: "On the board's Mac, apply the card changes a remote desk posts on the bus"}
	cmd.PersistentFlags().StringSliceVar(&from, "from", nil, "the lanes that may draw cards")
	if err := cmd.MarkPersistentFlagRequired("from"); err != nil {
		panic(err)
	}
	serve := &cobra.Command{
		Use:   "serve",
		Short: "Apply each card change addressed to " + LinkLane + " in order, resuming from its cursor",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			return Link{Runner: a.Runner, Drive: entry.Bus(), State: entry.StateDir, From: from}.Serve(c.Context())
		},
	}
	install := &cobra.Command{
		Use:   "install",
		Short: "Keep the link running under launchd",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			entry, _, err := opened()
			if err != nil {
				return err
			}
			agents, err := launchd.New(a.Runner, c.OutOrStdout())
			if err != nil {
				return err
			}
			home, err := os.UserHomeDir()
			if err != nil {
				return err
			}
			return agents.Install(c.Context(), launchd.Job{
				Label:     linkLabel(entry.Drive),
				Env:       [][2]string{{"PATH", os.Getenv("PATH")}},
				Arguments: []string{filepath.Join(home, ".claude", "long-running", "current", "bin", "lr"), "board", "--drive", entry.Drive, "link", "serve", "--from", strings.Join(from, ",")},
				Directory: entry.StateDir,
				Log:       filepath.Join(entry.StateDir, "board-link.log"),
			})
		},
	}
	cmd.AddCommand(serve, install)
	return cmd
}

func drawCommand(a *app.App, opened func() (drive.Entry, string, error), judge Judge) *cobra.Command {
	var lane, key, remove string
	cmd := &cobra.Command{
		Use:   "draw [FILE|-]",
		Short: "From any host, post one card change on the bus for the board link: a block as JSON, refused when its background or Explain more is only a link, when a card on his board already asks the same, when it asks him to grade a reading of his words, or when one of his ccn answers already settles it; or --remove ID",
		Args:  cobra.MaximumNArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			entry, session, err := opened()
			if err != nil {
				return err
			}
			var block []byte
			switch {
			case len(args) == 0:
			case args[0] == "-":
				block, err = io.ReadAll(c.InOrStdin())
			default:
				block, err = os.ReadFile(args[0])
			}
			if err != nil {
				return err
			}
			if len(block) > 0 {
				refused, err := CardsOf(a, entry, session, nil).Check(c.Context(), judge, block)
				if refused != nil {
					return refused
				}
				if err != nil {
					fmt.Fprintf(c.ErrOrStderr(), "WARNING the card draws unchecked against his answers: %s\n", strings.ReplaceAll(err.Error(), "\n", "; "))
				}
			}
			record, err := DrawCard(c.Context(), entry.Bus(), lane, key, session, block, remove)
			if err != nil {
				return err
			}
			fmt.Fprintf(c.OutOrStdout(), "#%d\n", record.Seq)
			return nil
		},
	}
	cmd.Flags().StringVar(&lane, "lane", "", "the lane the record posts as")
	cmd.Flags().StringVar(&key, "key", "", "what makes a repeat of this change harmless, such as the desk's owed row")
	cmd.Flags().StringVar(&remove, "remove", "", "remove this block rather than draw one")
	for _, name := range []string{"lane", "key"} {
		if err := cmd.MarkFlagRequired(name); err != nil {
			panic(err)
		}
	}
	return cmd
}
