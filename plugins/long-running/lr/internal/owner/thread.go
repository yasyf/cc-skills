package owner

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	threadProof = "proof:slack-thread:"
	noBoard     = "no cc-present artifact"
	rowWait     = 30 * time.Second
)

type threadWord struct {
	Channel string `json:"channel_id"`
	Thread  string `json:"thread_ts"`
	TS      string `json:"ts"`
	Text    string `json:"text"`
	Owner   *struct {
		Session string `json:"session"`
		Card    string `json:"card"`
		Title   string `json:"title"`
	} `json:"thread_owner"`
}

type wordCard struct {
	session, id, title, block string
}

func threadWordCommand(a *app.App, driveName *string) *cobra.Command {
	var session string
	cmd := &cobra.Command{
		Use:   "thread-word",
		Short: "Read one slack.message of his on stdin and, when it replies in a card's own Slack thread, write the from:owner answer row a tap writes, once per message",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			var word threadWord
			if err := json.NewDecoder(cmd.InOrStdin()).Decode(&word); err != nil {
				return fmt.Errorf("read the slack.message: %w", err)
			}
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			return word.record(cmd.Context(), a.Runner, entry, session)
		},
	}
	cmd.Flags().StringVar(&session, "session", os.Getenv("CLAUDE_CODE_SESSION_ID"), "the session this window runs, whose board holds the cards it posted")
	return cmd
}

func (w threadWord) record(ctx context.Context, runner shell.Runner, entry drive.Entry, session string) error {
	card, found, err := w.card(ctx, runner, session)
	if err != nil || !found || w.Text == "" {
		return err
	}
	seconds, err := strconv.ParseFloat(w.TS, 64)
	if err != nil {
		return fmt.Errorf("slack ts %q: %w", w.TS, err)
	}
	proof := threadProof + w.TS
	ccn := func(args ...string) (string, error) {
		done, err := runner.Run(ctx, shell.Command{Argv: append([]string{"ccn", "-R", entry.Checkout}, args...), Timeout: rowWait})
		return done.Stdout, err
	}
	return locked(Ledger(entry.StateDir), func() error {
		listed, err := ccn("answer", "list", "--label", proof, "--limit", "0", "--json")
		if err != nil {
			return err
		}
		var rows []json.RawMessage
		if strings.TrimSpace(listed) != "" {
			if err := json.Unmarshal([]byte(listed), &rows); err != nil {
				return fmt.Errorf("ccn answer list: %w", err)
			}
		}
		if len(rows) > 0 {
			return nil
		}
		_, err = ccn("answer", "add", card.title, "--body", w.body(card, time.Unix(int64(seconds), 0)),
			"--label", "from:owner",
			"--label", "board-answer",
			"--label", "program:"+cmp.Or(entry.Program, filepath.Base(entry.StateDir)),
			"--label", "board-session:"+card.session,
			"--label", "board-card:"+card.id,
			"--label", proof,
			"--json")
		return err
	})
}

func (w threadWord) body(card wordCard, at time.Time) string {
	block := ""
	if card.block != "" {
		block = "block " + card.block + ", "
	}
	return fmt.Sprintf("%s\nBoard question: %s\nCard %s, %sboard %s, typed in the card's Slack thread %s, %s PT.", w.Text, card.title, card.id, block, card.session, w.Thread, at.In(pacific).Format(rowTime))
}

func (w threadWord) card(ctx context.Context, runner shell.Runner, session string) (wordCard, bool, error) {
	if w.Owner != nil {
		return wordCard{session: w.Owner.Session, id: w.Owner.Card, title: cmp.Or(w.Owner.Title, w.Owner.Card)}, w.Owner.Card != "", nil
	}
	shown, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-present", "outcomes", "--all", "--session", session}, Timeout: rowWait})
	var failed *shell.ExitError
	if errors.As(err, &failed) && strings.Contains(failed.Stderr, noBoard) {
		return wordCard{}, false, nil
	}
	if err != nil {
		return wordCard{}, false, err
	}
	var outcomes struct {
		Doc struct {
			Blocks []struct {
				ID    string `json:"id"`
				Title string `json:"title"`
				Slack *struct {
					URL string `json:"url"`
				} `json:"slack"`
				Children []struct {
					ID   string `json:"id"`
					Type string `json:"type"`
				} `json:"children"`
			} `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &outcomes); err != nil {
		return wordCard{}, false, fmt.Errorf("cc-present outcomes: %w", err)
	}
	thread := "/archives/" + w.Channel + "/p" + strings.ReplaceAll(w.Thread, ".", "")
	for _, block := range outcomes.Doc.Blocks {
		if block.Slack == nil {
			continue
		}
		if link, _, _ := strings.Cut(block.Slack.URL, "?"); !strings.HasSuffix(link, thread) {
			continue
		}
		card := wordCard{session: session, id: block.ID, title: cmp.Or(block.Title, block.ID)}
		for _, child := range block.Children {
			if child.Type == "choice" {
				card.block = child.ID
			}
		}
		return card, true, nil
	}
	return wordCard{}, false, nil
}
