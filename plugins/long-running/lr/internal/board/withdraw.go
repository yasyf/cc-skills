package board

import (
	"context"
	"fmt"
	"io"
	"os"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var (
	tapTarget = regexp.MustCompile(`^(?:#?(\d+)|merge:(\d+):[0-9a-f]+)$`)
	namesID   = regexp.MustCompile(`#\d|\bcard-|\bmerge:\d`)
)

func (c Cards) Pull(ctx context.Context, lane, target, why string, out io.Writer) (bool, error) {
	if strings.TrimSpace(why) == "" || namesID.MatchString(why) {
		return false, fmt.Errorf("--why %q is what the owner reads where the card was: plain words, no card, PR or record ids", why)
	}
	named := tapTarget.FindStringSubmatch(target)
	if named == nil {
		return false, c.Retire(ctx, lane, why, []string{target}, out)
	}
	number, err := strconv.Atoi(named[1] + named[2])
	if err != nil {
		return false, err
	}
	prs, err := c.Store.NumberedPRs([]int{number})
	if err != nil {
		return false, err
	}
	if len(prs) == 0 {
		fmt.Fprintf(out, "UNCHANGED #%d: no PR of this drive holds the number\n", number)
		return false, nil
	}
	cards := []string{}
	if named[2] != "" {
		cards = append(cards, target)
	}
	pulled := false
	for _, pr := range prs {
		cards = append(cards, MergeWaits(pr.Number, pr.Head))
		stored, err := c.Store.Facts(pr.Repo, pr.Number, pr.Head)
		if err != nil {
			return false, err
		}
		if stored[FactWithdrawn] != "" {
			fmt.Fprintf(out, "UNCHANGED %s#%d at %s: its tap was already withdrawn\n", pr.Repo, pr.Number, short(pr.Head))
			continue
		}
		if _, err := c.Store.PutFact(pr.Repo, pr.Number, pr.Head, FactWithdrawn, why, c.Now()); err != nil {
			return false, err
		}
		if err := c.Withdraw(ctx, lane, fmt.Sprintf("%s#%d", pr.Repo, pr.Number), fmt.Sprintf("Withdrew the %s#%d tap at %s from the owner's queue: %s.", pr.Repo, pr.Number, short(pr.Head), why)); err != nil {
			return false, err
		}
		fmt.Fprintf(out, "WITHDRAWN %s#%d at %s: off the owner's merge list\n", pr.Repo, pr.Number, short(pr.Head))
		pulled = true
	}
	return pulled, c.Retire(ctx, lane, why, slices.Compact(cards), out)
}

func WithdrawCommand(a *app.App) *cobra.Command {
	var driveName, session, lane, why string
	cmd := &cobra.Command{
		Use:   "withdraw CARD_OR_PR",
		Short: "Take one card or landing tap off the owner's board, his merge list and its Slack card in one call; a rerun changes nothing",
		Long:  "CARD_OR_PR is a board card id such as card-<reply id>, a merge card id merge:<pr>:<sha>, or a PR number #<n>. A PR's tap is withdrawn at its current head, so a new head draws a fresh card.",
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
			_, err = CardsOf(a, entry, session, s).Pull(c.Context(), lane, args[0], why, c.OutOrStdout())
			return err
		},
	}
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
