package owner

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/reply"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	questionLane = "owner-board"
	coordinator  = "root"
	answerWithin = 15 * time.Minute
	postWait     = 30 * time.Second
)

var (
	holderWords = map[string]string{coordinator: "the coordinator", communicator: "your chat"}
	boardLead   = regexp.MustCompile(`^Owner on board \S+, `)
	cardID      = regexp.MustCompile(` \([\w:-]+\)(: |$)`)
)

type pageQuestion struct {
	words  string
	holder string
	due    time.Time
}

func newQuestion(cmd *cobra.Command, now time.Time, holder string) (pageQuestion, error) {
	if _, known := holderWords[holder]; !known {
		return pageQuestion{}, fmt.Errorf("a question is held by %s or %s, not %q", coordinator, communicator, holder)
	}
	words, err := io.ReadAll(cmd.InOrStdin())
	if err != nil {
		return pageQuestion{}, err
	}
	return pageQuestion{strings.TrimSpace(string(words)), holder, now.Add(answerWithin)}, nil
}

func (q pageQuestion) line() string {
	return fmt.Sprintf("Received, with %s, answer by %s", holderWords[q.holder], q.due.In(pacific).Format(byTime))
}

func (q pageQuestion) plan() []string {
	return []string{
		"--attr", "deadline=" + q.due.UTC().Format(time.RFC3339),
		"--attr", "owner=" + q.holder,
		"--attr", "step:1=" + q.due.In(pacific).Format(byTime) + " answer his question",
	}
}

func (q pageQuestion) spoken() string {
	said := cardID.ReplaceAllString(boardLead.ReplaceAllString(q.words, ""), "$1")
	return "> " + strings.ReplaceAll(said, "\n", "\n> ")
}

func (q pageQuestion) acknowledge(ctx context.Context, a *app.App, entry drive.Entry, routed int) (string, error) {
	row, err := Add(Ledger(entry.StateDir), a.Now(), q.words, duePrefix+q.due.In(pacific).Format(rowTime), routed)
	if err != nil {
		return "", err
	}
	var failed []error
	sent := reply.Fanout{Runner: a.Runner, Entry: entry, Now: a.Now}.Send(ctx, reply.Reply{ID: "received-" + row, Title: q.line(), MD: q.line() + "\n\n" + q.spoken()})
	for _, surface := range sent {
		if surface.Error != "" {
			failed = append(failed, fmt.Errorf("%s: %s", surface.Surface, surface.Error))
		}
	}
	argv := append([]string{"cci", "post", "--drive", entry.Bus(), "--lane", questionLane, "--kind", "posted", "--to", communicator, "--attr", "row=" + row, "--text", q.line()}, q.plan()...)
	if routed != 0 {
		argv = append(argv, "--re", strconv.Itoa(routed))
	}
	if _, err := a.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: postWait}); err != nil {
		failed = append(failed, err)
	}
	return row, errors.Join(failed...)
}

func questionCommands(a *app.App, driveName *string) []*cobra.Command {
	var pr, head, holder string
	var routed int
	ask := &cobra.Command{
		Use:   "ask",
		Short: "Route the question on stdin from his review page to the coordinator and the communicator, with its row and acknowledgement; prints the record's seq",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			q, err := newQuestion(cmd, a.Now(), coordinator)
			if err != nil {
				return err
			}
			argv := append([]string{"cci", "post", "--drive", entry.Bus(), "--lane", questionLane, "--kind", "ask", "--to", coordinator + "," + communicator, "--pr", pr, "--attr", "head=" + head, "--quoted", q.words, "--json"}, q.plan()...)
			done, err := a.Runner.Run(cmd.Context(), shell.Command{Argv: argv, Timeout: postWait})
			if err != nil {
				return err
			}
			var posted struct {
				Seq int `json:"seq"`
			}
			if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil {
				return fmt.Errorf("cci post named no record: %w: %s", err, done.Stdout)
			}
			fmt.Fprintln(cmd.OutOrStdout(), posted.Seq)
			_, err = q.acknowledge(cmd.Context(), a, entry, posted.Seq)
			return err
		},
	}
	ask.Flags().StringVar(&pr, "pr", "", "the pull request he asked on")
	ask.Flags().StringVar(&head, "head", "", "the head he was reading")
	received := &cobra.Command{
		Use:   "received",
		Short: "Give the question on stdin, routed already, its row and acknowledgement; prints the row",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			entry, err := drive.Named(*driveName)
			if err != nil {
				return err
			}
			q, err := newQuestion(cmd, a.Now(), holder)
			if err != nil {
				return err
			}
			row, err := q.acknowledge(cmd.Context(), a, entry, routed)
			fmt.Fprintln(cmd.OutOrStdout(), row)
			return err
		},
	}
	received.Flags().IntVar(&routed, "routed", 0, "the seq of the cci record that routed the question")
	received.Flags().StringVar(&holder, "holder", communicator, "who has it: root or owner-relay")
	return []*cobra.Command{ask, received}
}
