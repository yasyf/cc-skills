package board

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type Update struct {
	Repo    string
	Since   time.Time
	Landed  []store.ReviewRow
	Red     []store.ReviewRow
	Waiting []store.ReviewRow
	Cards   []OpenCard
}

func OwnerUpdate(entry drive.Entry, since time.Time, cards []OpenCard) (Update, error) {
	cfg, err := DriveConfig(entry)
	if err != nil {
		return Update{}, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return Update{}, err
	}
	defer func() { _ = s.Close() }()
	rows, err := s.ReviewRows(cfg.Repo)
	if err != nil {
		return Update{}, err
	}
	u := Update{Repo: cfg.Repo, Since: since, Cards: cards}
	for _, row := range rows {
		landed, err := store.Parse(row.LandedAt)
		switch {
		case row.State == prrow.StateLanded && err == nil && landed.After(since):
			u.Landed = append(u.Landed, row)
		case row.State != prrow.StateOpen:
		case waitsOnHim(row) && OffersApproval(row):
			u.Waiting = append(u.Waiting, row)
		case row.CI == prrow.CIRed:
			u.Red = append(u.Red, row)
		}
	}
	for _, list := range [][]store.ReviewRow{u.Landed, u.Red, u.Waiting} {
		slices.SortFunc(list, func(one, other store.ReviewRow) int { return one.Number - other.Number })
	}
	return u, nil
}

func (u Update) URLs() []string {
	urls := make([]string, len(u.Waiting))
	for n, row := range u.Waiting {
		urls[n] = fmt.Sprintf("https://github.com/%s/pull/%d", u.Repo, row.Number)
	}
	return urls
}

func (u Update) Words() string {
	named := func(row store.ReviewRow) string {
		return strings.TrimSpace(fmt.Sprintf("#%d %s", row.Number, PlainTitle(row.Title)))
	}
	section := func(heading, none string, lines []string) string {
		if len(lines) == 0 {
			return fmt.Sprintf("**%s**\n%s", heading, none)
		}
		return fmt.Sprintf("**%s**\n- %s", heading, strings.Join(lines, "\n- "))
	}
	landed := make([]string, 0, len(u.Landed))
	red := make([]string, 0, len(u.Red))
	waiting := make([]string, 0, len(u.Waiting)+len(u.Cards))
	for _, row := range u.Landed {
		landed = append(landed, named(row))
	}
	for _, row := range u.Red {
		red = append(red, named(row)+": its checks are red")
	}
	for _, row := range u.Waiting {
		waiting = append(waiting, named(row)+": Approve or Hold on its card")
	}
	for _, card := range u.Cards {
		waiting = append(waiting, card.Title+": your pick, on its card in this thread")
	}
	return strings.Join([]string{
		"Since " + pacific(u.Since) + ".",
		section("Landed", "Nothing landed.", landed),
		section("Red", "Nothing is red.", red),
		section("Waiting on you", "Nothing waits on you.", waiting),
	}, "\n\n")
}
