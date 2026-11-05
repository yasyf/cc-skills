package owner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	slackMessage      = "slack.message"
	slackAction       = "slack.action"
	slackWatchError   = "slack.watch_error"
	slackOwnerStopped = "slack.owner_stopped"
	fallbackPost      = "_(Yasyf's Claude)_"
	lookingAt         = "eyes"
	slackCall         = 15 * time.Second
	maxSlackEvent     = 4 << 20
	watching          = "owner Slack watch running for %s: each of his messages gets :eyes: and wakes this window"
)

type slackEvent struct {
	Type       string `json:"type"`
	Channel    string `json:"channel_id"`
	Thread     string `json:"thread_ts"`
	TS         string `json:"ts"`
	User       string `json:"user"`
	FromClaude bool   `json:"from_claude"`
	Text       string `json:"text"`
}

func (e slackEvent) fromBot() bool {
	return e.Type == slackMessage && (e.FromClaude || strings.HasPrefix(e.Text, fallbackPost))
}

func watchCommand(a *app.App, driveName *string) *cobra.Command {
	return &cobra.Command{
		Use:   "watch",
		Short: "Stream this window's Slack events under its standing Monitor, reacting :eyes: on each of his messages as it arrives and printing what his thread answers and what a bare link names; a bot's message wakes nothing",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			threads, closed, err := openThreads(*driveName)
			if err != nil {
				return err
			}
			defer closed()
			rows, err := wordRowsOn(a.Runner, *driveName)
			if err != nil {
				return err
			}
			return watch(cmd.Context(), a.Runner, threads, rows, cmd.OutOrStdout())
		},
	}
}

type rowsOf func(ctx context.Context, word threadWord) ([]string, error)

func wordRowsOn(runner shell.Runner, driveName string) (rowsOf, error) {
	entry, err := drive.Named(driveName)
	if errors.Is(err, drive.ErrNoDrive) {
		return func(context.Context, threadWord) ([]string, error) { return nil, nil }, nil
	}
	if err != nil {
		return nil, err
	}
	return func(ctx context.Context, word threadWord) ([]string, error) { return word.rows(ctx, runner, entry) }, nil
}

func watch(ctx context.Context, runner shell.Runner, threads threadsAt, rows rowsOf, out io.Writer) error {
	shown, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "whoami"}, Timeout: slackCall})
	if err != nil {
		return err
	}
	var me struct {
		User string `json:"user_id"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &me); err != nil {
		return fmt.Errorf("cc-slack whoami printed %q: %w", shown.Stdout, err)
	}
	if _, err := fmt.Fprintf(out, watching+"\n", me.User); err != nil {
		return err
	}
	return streamLines(ctx, runner, []string{"cc-slack", "watch", "--only-delivery", "--whole"}, func(line []byte) error {
		var event slackEvent
		if err := json.Unmarshal(line, &event); err != nil {
			return fmt.Errorf("cc-slack watch printed %q: %w", line, err)
		}
		if event.fromBot() {
			return nil
		}
		var written []string
		if event.Type == slackMessage && event.User == me.User {
			if _, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "react", "--channel", event.Channel, "--ts", event.TS, "--name", lookingAt}, Timeout: slackCall}); err != nil {
				fmt.Fprintln(out, clippedLine(fmt.Sprintf("%s did not land on his message %s: %v", lookingAt, event.TS, err)))
			}
			var word threadWord
			if err := json.Unmarshal(line, &word); err != nil {
				return fmt.Errorf("cc-slack watch printed %q: %w", line, err)
			}
			var err error
			if written, err = rows(ctx, word); err != nil {
				fmt.Fprintln(out, clippedLine(fmt.Sprintf("his message %s wrote no ledger row: %v", event.TS, err)))
			}
			var missed []string
			line, missed = joined(ctx, runner, threads, line, event)
			for _, miss := range missed {
				fmt.Fprintln(out, clippedLine(miss))
			}
		}
		line, err := fitted(line, written)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(out, string(line))
		return err
	})
}
