package supervisor

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	unansweredEvery = time.Minute
	unansweredAfter = 10 * time.Minute
)

type unanswered struct {
	runner shell.Runner
	store  *store.Store
	post   pass.Post
	now    func() time.Time
}

func (u unanswered) raise(ctx context.Context) error {
	shown, err := u.runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "dm-owed"}, Timeout: time.Minute})
	if err != nil {
		return err
	}
	var owed struct {
		Replies []struct {
			TS        string `json:"ts"`
			Text      string `json:"text"`
			Permalink string `json:"permalink"`
		} `json:"replies"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &owed); err != nil {
		return fmt.Errorf("cc-slack dm-owed printed %q: %w", shown.Stdout, err)
	}
	now := u.now()
	for _, reply := range owed.Replies {
		sent, err := strconv.ParseFloat(reply.TS, 64)
		if err != nil {
			return fmt.Errorf("cc-slack dm-owed: reply ts %q: %w", reply.TS, err)
		}
		waited := now.Sub(time.Unix(int64(sent), 0))
		if waited < unansweredAfter {
			continue
		}
		subject := "unanswered:" + reply.TS
		cause := fmt.Sprintf("UNANSWERED: his message %s has had no reply for %d min: %.80q; answer it in its thread", reply.Permalink, int(waited.Minutes()), reply.Text)
		raised, err := u.store.Escalate(subject, cause, lanes.OwnerRelay, now)
		if err != nil {
			return err
		}
		if !raised {
			continue
		}
		if err := u.post([]string{lanes.OwnerRelay, lanes.Root}, bus.Escalation{Subject: subject, Cause: cause}); err != nil {
			return err
		}
	}
	return nil
}
