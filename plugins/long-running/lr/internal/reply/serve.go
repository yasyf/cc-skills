package reply

import (
	"context"
	"encoding/json"
	"strconv"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-interact/channel"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

var schema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"id":          map[string]any{"type": "string", "description": "The id an earlier reply returned, to edit that reply in place on every surface. Omit it for a new reply."},
		"title":       map[string]any{"type": "string", "description": "One line: the card's header on his board and in Slack, and the phone notification."},
		"md":          map[string]any{"type": "string", "description": "The reply in markdown: prose, lists, tables and links."},
		"status":      map[string]any{"type": "string", "enum": statuses, "description": "Set when he must see this now; a reply with a status also goes to his Slack, except progress or info on an order with no picks, which writes his ledger row and posts nothing to Slack."},
		"picks":       map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "2 to 4 answers he picks from, each one tap button on his board and in Slack."},
		"recommended": map[string]any{"type": "string", "description": "The pick you recommend; required with picks."},
		"build":       map[string]any{"type": "object", "properties": map[string]any{"repo": map[string]any{"type": "string"}, "branch": map[string]any{"type": "string"}, "none": map[string]any{"type": "string"}}, "description": "The branch the recommended pick is built on, as repo OWNER/NAME and branch, or none with the reason it has nothing to build; his board and Slack card show it under that pick."},
		"links":       map[string]any{"type": "array", "items": map[string]any{"type": "object", "properties": map[string]any{"label": map[string]any{"type": "string"}, "url": map[string]any{"type": "string"}}, "required": []string{"label", "url"}}, "description": "Up to 5 links, such as a hosted one-pager; an https link is a button in Slack, an http one a line of the card."},
		"prs":         map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "Up to 10 GitHub pull request urls, each shown as its live PR card."},
		"thread":      map[string]any{"type": "string", "description": "The ts of the Slack DM thread this reply answers. An ask he sent as a thread reply this turn fills it in; name it only to answer a thread he wrote in on an earlier turn."},
		"order":       map[string]any{"type": "string", "description": "The ledger row, such as m347, of the owner order this reply answers. The tool then leads the reply with \"Done, nothing for you\", or with action; write md without that lead."},
		"action":      map[string]any{"type": "string", "description": "With order, the one thing he must do, on one line. Omit it when he need do nothing."},
		"by":          map[string]any{"type": "string", "description": "With order and status progress or info, the clock it is due by, such as 11:30 PM: the ledger row reads due by it. Omit it and the row reads responded."},
		"routed":      map[string]any{"type": "integer", "description": "With order and status progress or info, the seq of the cci record that routed his ask, written to the ledger row."},
	},
	"required": []string{"title", "md"},
}

func Command(a *app.App, commands func() *cobra.Command) *cobra.Command {
	reply := &cobra.Command{Use: "reply", Short: "The communicator's one write to the owner"}
	reply.AddCommand(&cobra.Command{
		Use:   "serve",
		Short: "Serve the reply tool over MCP on stdio, and in a drive's session the owner ledger, explain, ask-lane and asks verbs",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			tools := []channel.Tool{Tool(a)}
			if _, err := drive.Session(); err == nil {
				tools = append(tools, VerbTools(commands)...)
			}
			server := channel.NewServer(channel.ServerInfo{Name: "lr", Version: cmd.Root().Version}, tools)
			return server.Serve(cmd.Context(), cmd.InOrStdin(), cmd.OutOrStdout())
		},
	})
	return reply
}

func Tool(a *app.App) channel.Tool {
	return channel.Tool{
		Name: "reply",
		Description: "Send the owner one reply. It always reaches this window as the returned markdown, a cci record and the dashboard's store; " +
			"a reply with picks also becomes one card on his board that cc-present posts to his Slack DM with a button per pick, " +
			"and one with links, PRs or a status becomes a card at his Slack DM root. A progress or info reply to an order writes his ledger row, which his dashboard shows under the ask, and posts nothing to Slack. One answering his Slack thread reply posts there as text instead. " +
			"A tap comes back to this session. " +
			"A surface that fails is named in the result while the others still go; never resend for it.",
		InputSchema: schema,
		Handler: func(ctx context.Context, args json.RawMessage, _ func(string)) (string, bool) {
			var r Reply
			if err := json.Unmarshal(args, &r); err != nil {
				return "reply: " + err.Error(), true
			}
			if r.ID == "" {
				r.ID = "r" + strconv.FormatInt(a.Now().UnixMilli(), 36)
			}
			r = r.led()
			if err := r.check(); err != nil {
				return "reply: " + err.Error(), true
			}
			entry, err := drive.Session()
			if err != nil {
				return "reply: " + err.Error(), true
			}
			return receipt(r, Fanout{Runner: a.Runner, Entry: entry, Now: a.Now}.Send(ctx, r)), false
		},
	}
}
