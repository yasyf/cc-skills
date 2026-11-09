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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	slackMessage      = "slack.message"
	slackAction       = "slack.action"
	slackWatchError   = "slack.watch_error"
	slackOwnerStopped = "slack.owner_stopped"
	fallbackPost      = "_(Yasyf's Claude)_"
	slackCall         = 15 * time.Second
	maxSlackEvent     = 4 << 20
	watching          = "owner Slack watch running for %s: each of his messages gets :eyes: and the working line and wakes this window"
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

func watchCommand(a *app.App, driveName *string, lane LaneOf) *cobra.Command {
	return &cobra.Command{
		Use:   "watch",
		Short: "Stream this window's Slack events under its standing Monitor, picking up each of his messages as it arrives with :eyes: and the working line, holding it at 30 seconds unanswered, and printing what his thread answers and what a bare link names; a bot's message wakes nothing",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			threads, rows, dir, closed, err := openWords(a.Runner, *driveName, lane)
			if err != nil {
				return err
			}
			defer closed()
			return watch(cmd.Context(), a.Runner, threads, rows, desk{dir: dir, now: time.Now, after: func(d time.Duration, f func()) { time.AfterFunc(d, f) }}, cmd.OutOrStdout())
		},
	}
}

type rowsOf func(ctx context.Context, word threadWord) ([]string, []string, error)

func openWords(runner shell.Runner, driveName string, lane LaneOf) (threadsAt, rowsOf, string, func(), error) {
	entry, err := drive.Named(driveName)
	if errors.Is(err, drive.ErrNoDrive) {
		none := func(string) (store.SlackThread, bool, error) { return store.SlackThread{}, false, nil }
		return none, func(context.Context, threadWord) ([]string, []string, error) { return nil, nil, nil }, "", func() {}, nil
	}
	if err != nil {
		return nil, nil, "", nil, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return nil, nil, "", nil, err
	}
	rows := func(ctx context.Context, word threadWord) ([]string, []string, error) {
		route, err := word.route(s, lane, entry)
		if err != nil {
			return nil, nil, err
		}
		return word.rows(ctx, runner, entry, route)
	}
	return s.SlackThreadAt, rows, PickupDir(entry), func() { _ = s.Close() }, nil
}

func watch(ctx context.Context, runner shell.Runner, threads threadsAt, rows rowsOf, picks desk, out io.Writer) error {
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
		var written, routed []string
		if event.Type == slackMessage && event.User == me.User {
			if err := picks.pickUp(ctx, runner, event); err != nil {
				fmt.Fprintln(out, clippedLine(fmt.Sprintf("the pickup did not land on his message %s: %v", event.TS, err)))
			}
			var word threadWord
			if err := json.Unmarshal(line, &word); err != nil {
				return fmt.Errorf("cc-slack watch printed %q: %w", line, err)
			}
			var err error
			if written, routed, err = rows(ctx, word); err != nil {
				fmt.Fprintln(out, clippedLine(fmt.Sprintf("his message %s wrote no ledger row: %v", event.TS, err)))
			}
			var missed []string
			line, missed = joined(ctx, runner, threads, line, event)
			for _, miss := range missed {
				fmt.Fprintln(out, clippedLine(miss))
			}
		}
		line, err := fitted(line, written, routed)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(out, string(line))
		return err
	})
}
