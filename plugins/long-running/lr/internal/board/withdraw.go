package board

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const readingWindow = 10 * time.Minute

var (
	tapTarget = regexp.MustCompile(`^(?:#?(\d+)|merge:(\d+):[0-9a-f]+)$`)
	namesID   = regexp.MustCompile(`#\d|\bcard-|\bmerge:\d`)
)

func plainWhy(why string) error {
	if strings.TrimSpace(why) == "" || namesID.MatchString(why) {
		return fmt.Errorf("--why %q is what the owner reads where the card was: plain words, no card, PR or record ids", why)
	}
	return nil
}

func (c Cards) LanePull(ctx context.Context, lane, target, kept, why string, out io.Writer) error {
	if err := plainWhy(why); err != nil {
		return err
	}
	if kept != "" && tapTarget.MatchString(target) {
		return fmt.Errorf("--duplicate-of names the card that stays in place of a duplicate card, and %s is a PR's tap", target)
	}
	said, err := c.HisWord(ctx, target)
	if err != nil {
		return err
	}
	if said != "" {
		return fmt.Errorf("refused: %s, and withdraw it only after %d minutes with no word from him", said, int(readingWindow.Minutes()))
	}
	if kept == "" {
		_, err := c.Pull(ctx, lane, target, why, out)
		return err
	}
	post, err := c.slackPost(ctx, kept)
	if err != nil {
		return err
	}
	if post == "" {
		return fmt.Errorf("--duplicate-of %s has no Slack post for the withdrawn card to link; name the card that went to his DM", kept)
	}
	return c.Replace(ctx, lane, why, post, []string{target}, out)
}

func (c Cards) HisWord(ctx context.Context, target string) (string, error) {
	pulled, err := c.pulled(target)
	if err != nil {
		return "", err
	}
	for _, card := range pulled.cards {
		if said, err := c.hisWord(ctx, card); err != nil || said != "" {
			return said, err
		}
	}
	return "", nil
}

func (c Cards) hisWord(ctx context.Context, card string) (string, error) {
	post, err := c.slackPost(ctx, card)
	if err != nil || post == "" {
		return "", err
	}
	thread, err := c.slackThread(ctx, post)
	if err != nil {
		return "", err
	}
	him := ""
	for _, message := range thread.Messages {
		at, err := slackTime(message.TS)
		if err != nil {
			return "", err
		}
		ago := c.Now().Sub(at)
		if ago >= readingWindow {
			continue
		}
		did := "replied in"
		if !message.FromUser {
			if len(message.Raw.Reactions) == 0 {
				continue
			}
			if him == "" {
				if him, err = c.slackUser(ctx); err != nil {
					return "", err
				}
			}
			if !slices.ContainsFunc(message.Raw.Reactions, func(r slackReaction) bool { return slices.Contains(r.Users, him) }) {
				continue
			}
			did = "reacted to a message in"
		}
		return fmt.Sprintf("he %s %s's Slack thread %s ago, so he may be reading or answering it now; answer him in that thread first, %s", did, card, ago.Round(time.Second), thread.link(post, message.TS)), nil
	}
	return "", nil
}

type slackReaction struct {
	Users []string `json:"users"`
}

type slackThread struct {
	Channel  string `json:"channel_id"`
	Thread   string `json:"thread_ts"`
	Messages []struct {
		TS       string `json:"ts"`
		FromUser bool   `json:"from_user"`
		Text     string `json:"text"`
		Raw      struct {
			Reactions []slackReaction `json:"reactions"`
		} `json:"raw"`
	} `json:"messages"`
}

func (c Cards) slackThread(ctx context.Context, post string) (slackThread, error) {
	read, err := c.run(ctx, shell.Command{Argv: []string{"cc-slack", "thread", "--url", post, "--raw"}})
	if err != nil {
		return slackThread{}, err
	}
	var thread slackThread
	if err := json.Unmarshal([]byte(read), &thread); err != nil {
		return slackThread{}, fmt.Errorf("cc-slack thread --url %s: %w", post, err)
	}
	return thread, nil
}

func (t slackThread) link(post, ts string) string {
	base, _, _ := strings.Cut(post, "/archives/")
	return fmt.Sprintf("%s/archives/%s/p%s?thread_ts=%s&cid=%s", base, t.Channel, strings.ReplaceAll(ts, ".", ""), t.Thread, t.Channel)
}

func slackTime(ts string) (time.Time, error) {
	seconds, fraction, _ := strings.Cut(ts, ".")
	whole, err := strconv.ParseInt(seconds, 10, 64)
	if err != nil {
		return time.Time{}, fmt.Errorf("slack ts %q: %w", ts, err)
	}
	nanos, err := strconv.ParseInt((fraction + "000000000")[:9], 10, 64)
	if err != nil {
		return time.Time{}, fmt.Errorf("slack ts %q: %w", ts, err)
	}
	return time.Unix(whole, nanos), nil
}

func (c Cards) answeredInThread(ctx context.Context, card string, answers func(ts string, at time.Time) bool) (string, []string, error) {
	post, err := c.slackPost(ctx, card)
	if err != nil || post == "" {
		return "", nil, err
	}
	thread, err := c.slackThread(ctx, post)
	if err != nil {
		return "", nil, err
	}
	for _, message := range slices.Backward(thread.Messages) {
		if !message.FromUser {
			continue
		}
		at, err := slackTime(message.TS)
		if err != nil {
			return "", nil, err
		}
		if answers(message.TS, at) {
			return fmt.Sprintf("answered in its thread: “%s”", clipWords(message.Text, hisWordsCut)), []string{"--replaced-by", thread.link(post, message.TS)}, nil
		}
	}
	return "", nil, nil
}

func (c Cards) slackUser(ctx context.Context) (string, error) {
	said, err := c.run(ctx, shell.Command{Argv: []string{"cc-slack", "whoami"}})
	if err != nil {
		return "", err
	}
	var me struct {
		User string `json:"user_id"`
	}
	if err := json.Unmarshal([]byte(said), &me); err != nil {
		return "", fmt.Errorf("cc-slack whoami: %w", err)
	}
	return me.User, nil
}

func (c Cards) slackPost(ctx context.Context, card string) (string, error) {
	shown, err := c.present(ctx, "outcomes", "--block", card)
	if err != nil && strings.Contains(err.Error(), neverOnBoard) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	var outcomes struct {
		Doc struct {
			Blocks []struct {
				ID    string `json:"id"`
				Slack *struct {
					URL string `json:"url"`
				} `json:"slack"`
			} `json:"blocks"`
		} `json:"doc"`
	}
	if err := json.Unmarshal([]byte(shown), &outcomes); err != nil {
		return "", fmt.Errorf("cc-present outcomes --block %s: %w", card, err)
	}
	for _, block := range outcomes.Doc.Blocks {
		if block.ID == card && block.Slack != nil {
			return block.Slack.URL, nil
		}
	}
	return "", nil
}

func (c Cards) Pull(ctx context.Context, lane, target, why string, out io.Writer) (bool, error) {
	if err := plainWhy(why); err != nil {
		return false, err
	}
	pulled, err := c.pulled(target)
	if err != nil {
		return false, err
	}
	if !pulled.tap {
		return false, c.Retire(ctx, lane, why, pulled.cards, out)
	}
	if len(pulled.prs) == 0 {
		fmt.Fprintf(out, "UNCHANGED #%d: no PR of this drive holds the number\n", pulled.number)
		return false, nil
	}
	return c.withdrawTaps(ctx, lane, pulled.prs, pulled.cards, why, out)
}

type pullTarget struct {
	tap    bool
	number int
	prs    []store.ReviewRow
	cards  []string
}

func (c Cards) pulled(target string) (pullTarget, error) {
	named := tapTarget.FindStringSubmatch(target)
	if named == nil {
		return pullTarget{cards: []string{target}}, nil
	}
	number, err := strconv.Atoi(named[1] + named[2])
	if err != nil {
		return pullTarget{}, err
	}
	prs, err := c.Store.NumberedReviewRows([]int{number})
	if err != nil {
		return pullTarget{}, err
	}
	pulled := pullTarget{tap: true, number: number, prs: prs}
	if named[2] != "" {
		pulled.cards = append(pulled.cards, target)
	}
	for _, pr := range prs {
		pulled.cards = append(pulled.cards, MergeWaits(pr.Number, pr.Head), tapCardID(pr))
	}
	pulled.cards = slices.Compact(pulled.cards)
	return pulled, nil
}

func (c Cards) withdrawTaps(ctx context.Context, lane string, prs []store.ReviewRow, cards []string, why string, out io.Writer) (bool, error) {
	pulled := false
	for _, pr := range prs {
		stored, err := c.Store.Facts(pr.Repo, pr.Number, pr.Head)
		if err != nil {
			return false, err
		}
		if stored[FactWithdrawn] != "" {
			fmt.Fprintf(out, "UNCHANGED %s#%d at %s: its tap was already withdrawn\n", pr.Repo, pr.Number, short(pr.Head))
			continue
		}
		if err := prrow.Withdraw(c.Store, pr.Repo, pr.Number, pr.Head, why, c.Now()); err != nil {
			return false, err
		}
		if err := c.Withdraw(ctx, lane, fmt.Sprintf("%s#%d", pr.Repo, pr.Number), fmt.Sprintf("Withdrew the %s#%d tap at %s from the owner's queue: %s.", pr.Repo, pr.Number, short(pr.Head), why)); err != nil {
			return false, err
		}
		fmt.Fprintf(out, "WITHDRAWN %s#%d at %s: off the owner's merge list\n", pr.Repo, pr.Number, short(pr.Head))
		pulled = true
	}
	if err := c.redrawWithdrawn(ctx, prs, out); err != nil {
		return false, err
	}
	return pulled, c.Retire(ctx, lane, why, cards, out)
}

func (c Cards) redrawWithdrawn(ctx context.Context, prs []store.ReviewRow, out io.Writer) error {
	repos := map[string]bool{}
	for _, pr := range prs {
		repos[pr.Repo] = true
		drawn, err := c.Store.LatestFacts(pr.Repo, FactCardDrawn)
		if err != nil {
			return err
		}
		if drawn[pr.Number] == "" {
			continue
		}
		redrawing, err := c.redrawPost(ctx, pr.Repo, pr.Number)
		if err != nil {
			return err
		}
		if redrawing > 0 {
			fmt.Fprintf(out, "REDRAWN %s#%d: its Slack post reads withdrawn, with no buttons\n", pr.Repo, pr.Number)
		}
	}
	for _, repo := range slices.Sorted(maps.Keys(repos)) {
		r := ReadyCards{Cards: c, Repo: repo}
		lists, err := r.tapLists()
		if err != nil {
			return err
		}
		if len(lists) == 0 {
			continue
		}
		rows, err := c.Store.ReviewRows(repo)
		if err != nil {
			return err
		}
		facts, err := c.Store.FactsAtRowHeads(repo, rows)
		if err != nil {
			return err
		}
		if err := r.refreshLists(ctx, rows, facts, lists); err != nil {
			return err
		}
	}
	return nil
}

func WithdrawCommand(a *app.App) *cobra.Command {
	var driveName, session, lane, why, kept string
	cmd := &cobra.Command{
		Use:   "withdraw CARD_OR_PR",
		Short: "Take one card or landing tap off the owner's board, his merge list and its Slack card in one call, refusing while he writes in the card's Slack thread; a rerun changes nothing",
		Long:  "CARD_OR_PR is a board card id such as card-<reply id>, a merge card id merge:<pr>:<sha>, or a PR number #<n>. A PR's tap is withdrawn at its current head, so a new head draws a fresh card. A card, or a PR's tap card, is refused while his reply in its Slack thread, or his reaction on a message there, is under 10 minutes old; the refusal links his message, so answer him there.",
		Args:  cobra.ExactArgs(1),
		RunE: func(c *cobra.Command, args []string) error {
			entry, err := drive.Named(driveName)
			if err != nil {
				return err
			}
			if session == "" {
				session = entry.Root()
			}
			s, err := store.Open(entry.Store())
			if err != nil {
				return err
			}
			defer func() { _ = s.Close() }()
			return CardsOf(a, entry, session, s).LanePull(c.Context(), lane, args[0], kept, why, c.OutOrStdout())
		},
	}
	cmd.Flags().StringVar(&kept, "duplicate-of", "", "the card that stays when this one is its duplicate; the withdrawn card's Slack post then says Replaced and links that card's post")
	cmd.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	cmd.Flags().StringVar(&session, "session", "", "the board's session (default: the drive's coordinator)")
	cmd.Flags().StringVar(&lane, "lane", os.Getenv("CLAUDE_LONG_RUNNING_LANE"), "the lane the withdraw records post as")
	cmd.Flags().StringVar(&why, "why", "", "what the owner reads on the board and the Slack card where the card was, in plain words with no ids, such as \"being redone after review\"")
	for _, flag := range []string{"lane", "why"} {
		if err := cmd.MarkFlagRequired(flag); err != nil {
			panic(err)
		}
	}
	return cmd
}
