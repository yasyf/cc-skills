package reply

import (
	"bytes"
	"context"
	"encoding/json"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"syscall"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-interact/channel"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/arch"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
)

var schema = map[string]any{
	"type": "object",
	"properties": map[string]any{
		"id":          map[string]any{"type": "string", "description": "The id an earlier reply returned, to edit that reply in place on every surface it reached, its Slack message included whatever thread this call names. A failed edit is named in the result and posts nothing new. Omit it for a new reply."},
		"title":       map[string]any{"type": "string", "description": "One line: the card's header on his board and in Slack, and the phone notification."},
		"md":          map[string]any{"type": "string", "description": "The reply in markdown: prose, lists, tables and links."},
		"status":      map[string]any{"type": "string", "enum": statuses, "description": "Set when he must see this now; a reply with a status also goes to his Slack, except progress on an order with no picks, which writes his ledger row and posts nothing to Slack."},
		"type":        map[string]any{"type": "string", "enum": types, "description": "What this answer is on his Chat page: an explanation, an update, something for him, a question, an incident or a done. Omit it and the tool takes it from status and picks."},
		"diagram":     map[string]any{"type": "object", "description": "One architecture doc (arch/v1, the format of an arch fence for lr explain), drawn on his Chat page with no one-pager. Board, terminal and Slack link its https dashboard page, and every board and Slack card or Slack thread reply carrying it shows it as its picture in that post. A diagram that does not draw, such as a store whose medium is not memory, disk, bucket, keys or pages, refuses the reply before anything posts."},
		"picks":       map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "2 to 4 answers he picks from, each 'Keep it :: what picking it does', one tap button on his board and in Slack with those words beside it. A label is a short sentence-case phrase, never capitals, and the words after :: always draw beside it."},
		"recommended": map[string]any{"type": "string", "description": "The pick you recommend; required with picks."},
		"ask":         map[string]any{"type": "integer", "description": "With picks, the seq of the lane's ask or other cci record the card is drawn from. The card's row records it, and a later correction or head record whose re or resolves chain reaches it sends you a STALE ask to redraw or withdraw the card before he taps."},
		"build":       map[string]any{"type": "object", "properties": map[string]any{"repo": map[string]any{"type": "string"}, "branch": map[string]any{"type": "string"}, "none": map[string]any{"type": "string"}}, "description": "The branch the recommended pick is built on, as repo OWNER/NAME and branch, or none with the reason it has nothing to build; his board and Slack card show it under that pick, and his pick of it is his tap on the drive PR open on that branch, now or once it opens."},
		"message":     map[string]any{"type": "object", "properties": map[string]any{"to": map[string]any{"type": "string", "description": "The Slack user or channel id it goes to, such as U0123ABCDE."}, "text": map[string]any{"type": "string", "description": "The message exactly as it goes out, in Slack mrkdwn, written to the cc-slack post rules: the person as a <@U…> mention, short paragraphs, settings as a list, identifiers in backticks."}, "pick": map[string]any{"type": "string", "description": "The one of picks whose tap sends it."}}, "required": []string{"to", "text", "pick"}, "description": "With picks, the Slack message one pick sends for him to someone else, such as a direct message to a teammate; keep it out of md. The card shows the text as it goes out. A text cc-slack check-post flags, or one it could not check, draws nothing and the result lists the findings. His tap on that pick comes back to you as a SEND record whose body is the text: send it yourself with slack_send that turn and never hand it to a lane."},
		"blocks":      map[string]any{"type": "array", "items": map[string]any{"type": "object"}, "description": "Up to 8 live cc-present blocks drawn on Talk beneath md, such as a table with filter, sort, rowKey, details and actions, a chart, a diagram or a display.artifact, which takes kind html or svg and a source, never a PNG. Your window and Slack get md alone, so md must still say the answer."},
		"links":       map[string]any{"type": "array", "items": map[string]any{"type": "object", "properties": map[string]any{"label": map[string]any{"type": "string"}, "url": map[string]any{"type": "string"}}, "required": []string{"label", "url"}}, "description": "Up to 5 links, such as a hosted one-pager; an https link is a button in Slack, an http one a line of the card."},
		"prs":         map[string]any{"type": "array", "items": map[string]any{"type": "string"}, "description": "Up to 10 GitHub pull request urls. On a reply with no picks, every open PR it names here or in md joins prs by itself and draws his Approve at once, in CI or not: his tap is stored at that head, and landing still waits for green checks, the approving review and grades. A PR listed here that is merged or closed, another person's, outside the drive's PR ledger, or unread from GitHub refuses the reply with NO APPROVE before anything posts, naming each; name such a PR in md as a plain link instead. A held or already tapped one shows as a link. A PR on a reply with picks, or one he can approve, draws as a PR card from its ledger row alone: title, what it changes for a user, the plan row, the risk and what it waits on. A missing field is left out and this call's result names it in one warning line; its lane fixes the PR body, never the reply."},
		"artifacts":   map[string]any{"type": "array", "items": map[string]any{"type": "object", "properties": map[string]any{"title": map[string]any{"type": "string", "description": "One line, the heading he reads it under."}, "kind": map[string]any{"type": "string", "enum": artifactKinds, "description": "code keeps the file's bytes with line numbers, for code, logs and plain text. transcript takes cc-transcript show --json output and keeps its turns. For a session's thinking pass --thinking and leave off --signal, which drops it. image takes a PNG, JPEG, GIF or WebP file and uploads it to the Slack post as a file; a pick card carries none, so send an image in a reply with no picks."}, "file": map[string]any{"type": "string", "description": "Absolute path on this host, read once when the reply goes out and copied, so a wiped scratch dir breaks no link."}, "language": map[string]any{"type": "string", "description": "For code, the label shown; the default is the file's extension."}}, "required": []string{"title", "kind", "file"}}, "description": "Up to 5 files he reads whole, 4 with a diagram, such as a transcript, code or a session's thinking, when a line of text cannot carry them. Each opens in a window on his Chat page and from a card or link in Slack, on his tailnet only; for code and transcript Slack gets the title, kind and size and never the body, and an image goes up as the picture itself. Words claiming a screenshot, image or picture refuse the reply unless an image or diagram goes with that post. An edit that leaves one off deletes its copy."},
		"thread":      map[string]any{"type": "string", "description": "The ts of the Slack DM thread this reply answers, or root for his DM root. The row an order or on names sets it: a row from a Slack message posts in that message's thread, his DM root messages included, and a terminal, board or Talk row at his DM root. An edit of a root reply whose order row came from his Slack message moves it under that message and withdraws the root copy; a reply meant for the root keeps editing there. A row with no recorded place refuses until this names one. Otherwise name it only to answer a thread whose row the reply does not name."},
		"order":       map[string]any{"type": "string", "description": "The ledger row, such as m347, of the owner order this reply answers. The tool leads the reply with action when he must act; write md without that lead. A reply without action, picks or not, reads as its own text."},
		"alert":       map[string]any{"type": "string", "description": "On every incident line, the incident id its lanes are named for, such as sentry-7830378865 for sentry-7830378865-fix. The first reply naming it posts as usual and opens the alert's thread; every later one posts in that Slack thread and under that first post on his Chat page, whatever thread or order it names. An incident reply without it warns."},
		"action":      map[string]any{"type": "string", "description": "With order, the one thing he must do, on one line. Omit it when he need do nothing."},
		"by":          map[string]any{"type": "string", "description": "With order and status progress or info and no picks, the clock it is due by, such as 11:30 PM: the ledger row reads due by it. Omit it and the row reads responded."},
		"routed":      map[string]any{"type": "integer", "description": "With order and status progress or info and no picks, the seq of the cci record that routed his ask, written to the ledger row."},
		"unprompted":  map[string]any{"type": "boolean", "description": "True when this message answers nothing he asked. Every other reply names its row in order or its line in on, and the tool warns on one naming neither."},
		"on":          map[string]any{"type": "string", "description": "The dashboard chat message this reply answers in its thread: the topic of his thread line, such as reply:<id> or line:m<n>. Omit it for a reply in the main conversation."},
	},
	"required": []string{"title", "md"},
}

func Command(a *app.App, commands func() *cobra.Command) *cobra.Command {
	reply := &cobra.Command{Use: "reply", Short: "The communicator's one write to the owner"}
	var resumed bool
	serve := &cobra.Command{
		Use:   "serve",
		Short: "Serve the reply tool and the owner ledger, explain, ask-lane and asks verbs over MCP on stdio; a frame that arrives after a newer lr is installed moves the server to it on the same stdio",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			tools := append([]channel.Tool{Tool(a)}, VerbTools(commands)...)
			stdin, err := newHandoff(cmd.Context(), a.Runner, a.Log, cmd.InOrStdin())
			if err != nil {
				return err
			}
			server := channel.NewServer(channel.ServerInfo{Name: "lr", Version: cmd.Root().Version, Resumed: resumed}, stdin.counted(tools))
			if err := server.Serve(cmd.Context(), stdin, cmd.OutOrStdout()); err != nil || stdin.moved == "" {
				return err
			}
			return syscall.Exec(stdin.moved, []string{stdin.moved, "reply", "serve", "--resumed"}, os.Environ())
		},
	}
	serve.Flags().BoolVar(&resumed, "resumed", false, "take over a session an older lr already initialized on this stdio")
	reply.AddCommand(serve)
	return reply
}

func Tool(a *app.App) channel.Tool {
	return channel.Tool{
		Name: "reply",
		Description: "Send the owner one reply. It always reaches this window as the returned markdown, a cci record and the dashboard's store; " +
			"a reply with picks also becomes one card on his board that cc-present posts to his Slack DM with a button per pick, " +
			"and one with links, PRs, artifacts or a status becomes a card at his Slack DM root. A progress reply to an order writes his ledger row, which his dashboard shows under the ask, and posts nothing to Slack. One answering his Slack thread reply posts there as text instead, and one whose row has no recorded place refuses until thread names it. An incident line naming its alert posts under that alert's first post by itself, in Slack and on Chat.A reply carrying an earlier id edits the Slack message it already posted and posts a new one only when it has none. " +
			"A tap comes back to this session. " +
			"A surface that fails is named in the result while the others still go; never resend for it unless its line says nothing went out, and then send it again with its id.\n" +
			"Before sending:\n" +
			"- Plain words, or it refuses: with picks, a title that is one question in one sentence; options of two sentences at most; a body that asks no second question, never opens by repeating the title, says no paragraph twice and puts counts in a table; no record id, plan step, lane name, hash, corpus row such as C211, build or job name, test function name or code term, so a test is named by what it checks.\n" +
			"- Saying something waits on his yes, no, pick or tap? Pass it in picks or prs, or name the open card it waits on by its title or its id, such as rmw64a1tv, and the reply copies that card into its own Slack thread; with none of them it refuses and names the nearest open card.\n" +
			"- A release word? Say what users get and link its Platy #releases permalink, or it warns. A lane's name is no release word.\n" +
			"- Describing a change? Name the concrete things and answer his first question, or open a line with Unproven: for a fact nobody has yet. A flagged reply posts with a warning naming the question; edit it with its id.\n" +
			"- A cci record number in md moves to a hidden cite by itself; write Sentry and other ids as links, a notebook as [title](url).\n" +
			"- by and routed need order, no picks and status progress or info.\n" +
			"- Picks drawn from a lane's ask or record? Pass its seq in ask, so a correction to it reaches you before he taps.\n" +
			"- A pick that sends a message for him to someone else? Pass it in message, never in md; it must pass cc-slack check-post or nothing draws, and his tap on it comes back as SEND, which you send yourself that turn.\n" +
			"- Every open PR you name draws his Approve now, in CI or not; never tell him a button comes later.\n" +
			"- Naming an open bug? List each fix PR he should tap in prs and say what it does; a PR you do not list never joins the card, and a line in the result names any you left out.\n" +
			"- The first reply after he has been away an hour opens with the dashboard link on line one.",
		InputSchema: schema,
		Handler: func(ctx context.Context, args json.RawMessage, _ func(string)) (string, bool) {
			var r Reply
			decoder := json.NewDecoder(bytes.NewReader(args))
			decoder.DisallowUnknownFields()
			if err := decoder.Decode(&r); err != nil {
				return "reply: " + err.Error() + "; its fields are " + strings.Join(slices.Sorted(maps.Keys(schema["properties"].(map[string]any))), ", "), true
			}
			if err := r.plain(); err != nil {
				return "reply: " + err.Error(), true
			}
			r, warned := r.mended()
			entry, err := drive.Session()
			if err != nil {
				return "reply: " + err.Error(), true
			}
			claude, err := drive.ConfigDir()
			if err != nil {
				return "reply: " + err.Error(), true
			}
			fanout := Fanout{Runner: a.Runner, Entry: entry, Now: a.Now, Log: a.Log, Judge: board.PackJudge(a.Runner), Shoot: arch.Draw, Cards: board.CardsOf(a, entry, entry.Root(), nil), Rulings: filepath.Join(claude, "long-running", "current", "bin", "rulings.py")}
			r, warning := fanout.underAlert(r)
			if warning != "" {
				warned = append(warned, warning)
			}
			r, err = fanout.placed(r)
			if err != nil {
				return "reply: " + err.Error(), true
			}
			r = r.led()
			if r.Order == "" && r.On == "" && !r.Unprompted {
				warned = append(warned, "this names no order, so Chat lists it by time; pass his row in order, or set unprompted if he asked nothing")
			}
			if r.MD == "" {
				return "reply: md carries the reply itself", true
			}
			if err := r.bareExplainMore(); err != nil {
				return "reply: " + err.Error(), true
			}
			if r, err = fanout.checkedMessage(ctx, r); err != nil {
				return "reply: " + err.Error(), true
			}
			if r.ID == "" {
				r.ID = "r" + strconv.FormatInt(a.Now().UnixMilli(), 36)
			}
			r, warning = fanout.withBugFixes(ctx, r)
			if warning != "" {
				warned = append(warned, warning)
			}
			r, warning = fanout.withNamedPRs(r)
			if warning != "" {
				warned = append(warned, warning)
			}
			r, warning = fanout.withApprovedPR(r)
			if warning != "" {
				warned = append(warned, warning)
			}
			if err := fanout.untappable(ctx, r); err != nil {
				return "reply: " + err.Error(), true
			}
			r, warning = fanout.withCardLinks(ctx, r)
			if warning != "" {
				warned = append(warned, warning)
			}
			if err := fanout.nothingToTap(ctx, r); err != nil {
				return "reply: " + err.Error(), true
			}
			if err := fanout.approvesTapped(r); err != nil {
				return "reply: " + err.Error(), true
			}
			if err := r.unreadAsk(); err != nil {
				return "reply: " + err.Error(), true
			}
			if err := r.strayMarkup(); err != nil {
				return "reply: " + err.Error(), true
			}
			if r, err = fanout.pictured(ctx, r); err != nil {
				return "reply: " + err.Error(), true
			}
			if warning := fanout.tapWarning(r); warning != "" {
				warned = append(warned, warning)
			}
			if warning := fanout.rulingsWarning(ctx, r); warning != "" {
				warned = append(warned, warning)
			}
			r, warning = fanout.withPortOpening(r)
			if warning != "" {
				warned = append(warned, warning)
			}
			if r, warning, err = fanout.withPRCards(ctx, r); err != nil {
				return "reply: " + err.Error(), true
			}
			if warning != "" {
				warned = append(warned, warning)
			}
			r, warning = r.untiedApprovals()
			if warning != "" {
				warned = append(warned, warning)
			}
			r, warning = fanout.withArtifacts(ctx, r)
			if warning != "" {
				warned = append(warned, warning)
			}
			if err := r.unshownPicture(); err != nil {
				return "reply: " + err.Error(), true
			}
			r, warning = fanout.withPRTitles(ctx, r)
			if warning != "" {
				warned = append(warned, warning)
			}
			r, warning = fanout.withPostLines(ctx, r)
			if warning != "" {
				warned = append(warned, warning)
			}
			sent := receipt(r, fanout.Send(ctx, r))
			if len(warned) > 0 {
				sent = "Warning, posted anyway: " + strings.Join(warned, "; ") + ".\n" + sent
			}
			return sent, false
		},
	}
}
