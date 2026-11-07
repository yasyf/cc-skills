package reply

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func (f Fanout) untappable(ctx context.Context, r Reply) error {
	if len(r.PRs) == 0 {
		return nil
	}
	cfg, err := board.DriveConfig(f.Entry)
	if err != nil {
		return fmt.Errorf("the drive's config did not read, so no PR in prs was checked for a button: %w", err)
	}
	numbers := make([]int, len(r.PRs))
	for n, pr := range r.PRs {
		numbers[n], _ = strconv.Atoi(pullURL.FindStringSubmatch(pr)[2])
	}
	_, rows, err := board.Rows(f.Entry, numbers)
	if err != nil {
		return fmt.Errorf("the PR ledger did not read, so no PR in prs was checked for a button: %w", err)
	}
	var unread []int
	for _, row := range rows {
		if !prrow.Terminal(row.State) && prrow.WordsWait(row) != "" {
			unread = append(unread, row.Number)
		}
	}
	var missed error
	if len(unread) > 0 {
		_, missed = board.ReadPRs(ctx, &app.App{Runner: f.Runner, Log: f.Log, Now: f.Now}, f.Entry, cfg, unread)
		if _, rows, err = board.Rows(f.Entry, numbers); err != nil {
			return fmt.Errorf("the PR ledger did not read after this reply read GitHub, so no PR in prs was checked for a button: %w", err)
		}
	}
	var refused []string
	stillUnread := false
	for n, pr := range r.PRs {
		repo := pullURL.FindStringSubmatch(pr)[1]
		at := slices.IndexFunc(rows, func(row store.ReviewRow) bool {
			return row.Number == numbers[n] && strings.EqualFold(repo, cfg.Repo)
		})
		switch {
		case at >= 0 && rows[at].State == prrow.StateLanded:
			refused = append(refused, pr+" is merged")
		case at >= 0 && rows[at].State == prrow.StateClosed:
			refused = append(refused, pr+" is closed")
		case at >= 0 && prrow.WordsWait(rows[at]) != "":
			stillUnread = true
			refused = append(refused, pr+" "+prrow.WordsWait(rows[at])+" after this reply read GitHub for it, so its card would print a title or body the PR has replaced; send it again and the reply reads again")
		case at < 0:
			refused = append(refused, pr+" "+f.unrowed(ctx, repo, numbers[n], cfg.Repo, cfg.Author))
		}
	}
	if len(refused) == 0 {
		return nil
	}
	refusal := "NO APPROVE: each PR in prs must draw his Approve, and these cannot, so nothing went out; take them off prs, naming any as a plain link in md: " + strings.Join(refused, "; ")
	if stillUnread && missed != nil {
		refusal += "; this reply's read of GitHub failed: " + missed.Error()
	}
	return errors.New(refusal)
}

func (f Fanout) unrowed(ctx context.Context, repo string, number int, driveRepo, author string) string {
	out, err := ccx.CLI{Runner: f.Runner}.GH(ctx, ccx.MaxAgeCached, "pr", "view", strconv.Itoa(number), "--repo", repo, "--json", "author,state")
	var pr struct {
		State  string `json:"state"`
		Author struct {
			Login string `json:"login"`
		} `json:"author"`
	}
	if err == nil {
		err = json.Unmarshal([]byte(out), &pr)
	}
	switch {
	case err != nil:
		return "did not read from GitHub, so whether it can take his Approve is unknown: " + err.Error()
	case pr.State != "OPEN":
		return "is " + strings.ToLower(pr.State)
	case pr.Author.Login != author:
		return "is " + pr.Author.Login + "'s, and another person's PR stays off his list"
	case !strings.EqualFold(repo, driveRepo):
		return "is in " + repo + ", which the drive's PR ledger does not hold, so no card can carry its Approve"
	}
	return "has no row in the drive's PR ledger, so no card carries its Approve; the lane that opened it posts its opened record with its head, then sends again"
}
