package waste

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

func owedReplies(ctx context.Context, runner shell.Runner, cfg Config, peers []client.Peer, now time.Time) ([]Finding, error) {
	shown, err := runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "dm-owed"}, Timeout: readLimit})
	if err != nil {
		return nil, err
	}
	var owed struct {
		Replies []struct {
			TS        string `json:"ts"`
			Text      string `json:"text"`
			Permalink string `json:"permalink"`
		} `json:"replies"`
		Holder string `json:"holder_session"`
	}
	if err := json.Unmarshal([]byte(shown.Stdout), &owed); err != nil {
		return nil, fmt.Errorf("cc-slack dm-owed printed %q: %w", shown.Stdout, err)
	}
	holder := "no holder"
	for _, peer := range peers {
		if peer.Session == owed.Holder && peer.Agent == "" {
			holder = cmp.Or(peer.Lane, holder)
		}
	}
	var findings []Finding
	for _, reply := range owed.Replies {
		sent, err := strconv.ParseFloat(reply.TS, 64)
		if err != nil {
			return nil, fmt.Errorf("cc-slack dm-owed: reply ts %q: %w", reply.TS, err)
		}
		age := int64(now.Sub(time.Unix(int64(sent), 0)).Minutes())
		if age >= cfg.Floors.OwedReplies {
			findings = append(findings, Finding{signalOwedReply, signalOwedReply + "/" + reply.TS, age, "minutes", holder, fmt.Sprintf("%s %.60q", reply.Permalink, reply.Text), cfg.Floors.OwedReplies})
		}
	}
	return findings, nil
}
