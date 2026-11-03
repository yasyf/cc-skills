package board

import (
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	slackCardBody = 2800
	mergeLabel    = "Merge"
	holdLabel     = "Hold"
)

type SlackRef struct {
	URL   string `json:"url"`
	Label string `json:"label"`
}

type SlackCard struct {
	Status      string     `json:"status"`
	Title       string     `json:"title"`
	Body        string     `json:"body"`
	Refs        []SlackRef `json:"refs"`
	Options     []string   `json:"options"`
	Recommended string     `json:"recommended"`
	Waits       string     `json:"waits"`
}

type SlackPR struct {
	Repo   string `json:"repo"`
	Number int    `json:"number"`
}

type slackAsk struct {
	PRs    []SlackPR `json:"prs"`
	Answer *struct {
		SlackPR
		Waits string `json:"waits"`
		Label string `json:"label"`
	} `json:"answer"`
}

type SlackPRCard struct {
	SlackPR
	Card SlackCard `json:"card"`
}

func Card(repo string, rows []Row, number int) (SlackCard, bool) {
	at := slices.IndexFunc(rows, func(row Row) bool { return row.PR == number })
	if at < 0 {
		return SlackCard{}, false
	}
	row := prRow(rows[at])
	body := fmt.Sprintf("*%s*\n*What changes:* %s\n*Against the plan:* %s\n*Risk:* %s\n*Review and CI:* %s\n*Blocks landing:* %s",
		row.Title, row.Change, row.Plan, row.Risk, row.Review, row.Blocker)
	if utf8.RuneCountInString(body) > slackCardBody {
		body = string([]rune(body)[:slackCardBody-1]) + "…"
	}
	waits := MergeWaits(number, rows[at].Head)
	return SlackCard{
		Status: "needs_you", Title: fmt.Sprintf("Merge #%d at %s?", number, rows[at].Head[:min(waitsSHA, len(rows[at].Head))]), Body: body,
		Refs:    []SlackRef{{URL: fmt.Sprintf("https://github.com/%s/pull/%d", repo, number), Label: fmt.Sprintf("%s#%d", repo, number)}},
		Options: []string{mergeLabel, holdLabel}, Recommended: mergeLabel, Waits: waits,
	}, true
}

func Respond(s *store.Store, repo string, hooks Hooks, rows []Row, number int, waits, label string, at time.Time) error {
	verdict, known := map[string]string{mergeLabel: verdictApproved, holdLabel: verdictRejected}[label]
	if !known {
		return fmt.Errorf("a merge card answers %s or %s, not %q", mergeLabel, holdLabel, label)
	}
	found := slices.IndexFunc(rows, func(row Row) bool { return row.PR == number })
	if found < 0 {
		return fmt.Errorf("#%d no longer waits on the owner's tap", number)
	}
	if now := MergeWaits(number, rows[found].Head); now != waits {
		return fmt.Errorf("#%d moved to %s since the card asked %s; its new card asks again", number, short(rows[found].Head), waits)
	}
	return Answer(s, repo, hooks, rows[found], verdict, at)
}

func slackCardCommand(a *app.App) *cobra.Command {
	var lane string
	cmd := &cobra.Command{
		Use:   "card",
		Short: "Read {\"prs\": [...]} or {\"answer\": {...}} on stdin: print the merge-row card for each PR that waits on the owner, or apply his Merge or Hold",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			var ask slackAsk
			if err := json.NewDecoder(c.InOrStdin()).Decode(&ask); err != nil {
				return fmt.Errorf("read the card request: %w", err)
			}
			live, err := drive.Live()
			if err != nil {
				return err
			}
			if k := ask.Answer; k != nil {
				at := slices.IndexFunc(live, func(entry drive.Entry) bool { return strings.EqualFold(entry.Repo, k.Repo) })
				if at < 0 {
					return fmt.Errorf("no live drive holds %s#%d", k.Repo, k.Number)
				}
				return withRows(c, a, live[at], func(s *store.Store, repository string, rows []Row) error {
					return Respond(s, repository, postHooks(c.Context(), live[at].Bus(), lane, repository), rows, k.Number, k.Waits, k.Label, a.Now())
				})
			}
			cards := []SlackPRCard{}
			for _, entry := range live {
				held := slices.DeleteFunc(slices.Clone(ask.PRs), func(pr SlackPR) bool { return !strings.EqualFold(pr.Repo, entry.Repo) })
				if len(held) == 0 {
					continue
				}
				if err := withRows(c, a, entry, func(_ *store.Store, repository string, rows []Row) error {
					for _, pr := range held {
						if card, ok := Card(repository, rows, pr.Number); ok {
							cards = append(cards, SlackPRCard{pr, card})
						}
					}
					return nil
				}); err != nil {
					return err
				}
			}
			return json.NewEncoder(c.OutOrStdout()).Encode(map[string][]SlackPRCard{"cards": cards})
		},
	}
	cmd.Flags().StringVar(&lane, "lane", "", "the lane the tap and hold records post as")
	if err := cmd.MarkFlagRequired("lane"); err != nil {
		panic(err)
	}
	return cmd
}

func withRows(c *cobra.Command, a *app.App, entry drive.Entry, use func(*store.Store, string, []Row) error) error {
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	repository, snapshot, err := snapshotOf(c.Context(), a, entry, s, c.ErrOrStderr())
	if err != nil {
		return err
	}
	rows, err := Rows(s, repository, snapshot)
	if err != nil {
		return err
	}
	return use(s, repository, rows)
}
