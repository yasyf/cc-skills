package owner

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	slackMessage  = "slack.message"
	fallbackPost  = "_(Yasyf's Claude)_"
	lookingAt     = "eyes"
	slackCall     = 15 * time.Second
	maxSlackEvent = 4 << 20
)

type slackEvent struct {
	Type       string `json:"type"`
	Channel    string `json:"channel_id"`
	TS         string `json:"ts"`
	User       string `json:"user"`
	FromClaude bool   `json:"from_claude"`
	Text       string `json:"text"`
}

func (e slackEvent) fromBot() bool {
	return e.Type == slackMessage && (e.FromClaude || strings.HasPrefix(e.Text, fallbackPost))
}

func watchCommand(a *app.App) *cobra.Command {
	return &cobra.Command{
		Use:   "watch",
		Short: "Stream this window's Slack events under its standing Monitor, reacting :eyes: on each of his messages as it arrives; a bot's message wakes nothing",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return watch(cmd.Context(), a.Runner, cmd.OutOrStdout())
		},
	}
}

func watch(ctx context.Context, runner shell.Runner, out io.Writer) error {
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
	read, write := io.Pipe()
	go func() {
		_, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "watch"}, Stdout: write})
		_ = write.CloseWithError(err)
	}()
	events := bufio.NewScanner(read)
	events.Buffer(make([]byte, 0, bufio.MaxScanTokenSize), maxSlackEvent)
	for events.Scan() {
		var event slackEvent
		if err := json.Unmarshal(events.Bytes(), &event); err != nil {
			return fmt.Errorf("cc-slack watch printed %q: %w", events.Text(), err)
		}
		if event.fromBot() {
			continue
		}
		if event.Type == slackMessage && event.User == me.User {
			if _, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "react", "--channel", event.Channel, "--ts", event.TS, "--name", lookingAt}, Timeout: slackCall}); err != nil {
				fmt.Fprintf(out, "%s did not land on his message %s: %v\n", lookingAt, event.TS, err)
			}
		}
		if _, err := fmt.Fprintln(out, events.Text()); err != nil {
			return err
		}
	}
	return events.Err()
}
