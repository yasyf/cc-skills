package reply

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var slackID = regexp.MustCompile(`^[UWCDG][A-Z0-9]{8,}$`)

type Message struct {
	To   string `json:"to"`
	Text string `json:"text"`
	Pick string `json:"pick"`
}

func (f Fanout) checkedMessage(ctx context.Context, r Reply) (Reply, error) {
	if r.Message == nil {
		return r, nil
	}
	m := *r.Message
	pick, _, _ := strings.Cut(m.Pick, pickSplit)
	m.Pick, m.Text = shortened(strings.TrimSpace(pick), maxPick), strings.TrimSpace(m.Text)
	if !slices.Contains(r.Picks, m.Pick) {
		return r, fmt.Errorf("message.pick %q is none of this card's picks, so no tap would send it and nothing went out; name the pick that sends it", m.Pick)
	}
	if !slackID.MatchString(m.To) {
		return r, fmt.Errorf("message.to %q is not a Slack user or channel id such as U0123ABCDE, so nothing went out; look the person up with slack_users", m.To)
	}
	checked, err := f.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "check-post", "-"}, Stdin: m.Text, Timeout: postTimeout})
	var exit *shell.ExitError
	switch {
	case errors.As(err, &exit) && exit.Code == 1 && strings.TrimSpace(checked.Stdout) != "":
		return r, fmt.Errorf("DRAFT FAILS CHECK-POST: the message this card would send breaks the cc-slack post rules, so nothing went out; fix each line and send again:\n%s", strings.TrimSpace(checked.Stdout))
	case err != nil:
		return r, fmt.Errorf("DRAFT UNCHECKED: cc-slack check-post did not run on the message, so nothing went out: %w", err)
	}
	r.Message = &m
	r.MD += "\n\n```\n" + m.Text + "\n```"
	return r, nil
}

func (f Fanout) recordSend(ctx context.Context, card string, r Reply) error {
	path := filepath.Join(f.Entry.StateDir, storeDir, r.ID+".message")
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	if err := os.WriteFile(path, []byte(r.Message.Text+"\n"), 0o600); err != nil {
		return err
	}
	return f.Cards.RecordSend(ctx, card, r.Message.Pick, r.Message.To, path)
}
