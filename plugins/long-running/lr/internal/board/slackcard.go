package board

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	approveLabel = "Approve"
	mergeLabel   = "Merge"
	holdLabel    = "Hold"
)

type SlackCard struct {
	Status      string       `json:"status"`
	Title       string       `json:"title"`
	PRs         []SlackPRRow `json:"prs"`
	Options     []string     `json:"options"`
	Recommended string       `json:"recommended"`
	Waits       string       `json:"waits"`
}

type SlackPRRow struct {
	URL    string `json:"url"`
	Title  string `json:"title,omitempty"`
	Head   string `json:"head,omitempty"`
	Review string `json:"review,omitempty"`
	Change string `json:"change"`
	Plan   string `json:"plan"`
	Risk   string `json:"risk"`
	Blocks string `json:"blocks"`
}

type SlackPR struct {
	Repo   string `json:"repo"`
	Number int    `json:"number"`
}

type slackAsk struct {
	PRs     []SlackPR `json:"prs"`
	Numbers []int     `json:"numbers"`
	Answer  *struct {
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
	title := row.Title
	if title == fmt.Sprintf("#%d", number) {
		title = ""
	}
	return SlackCard{
		Status: "needs_you", Title: fmt.Sprintf("Approve #%d at %s?", number, rows[at].Head[:min(waitsSHA, len(rows[at].Head))]),
		PRs: []SlackPRRow{{
			URL: fmt.Sprintf("https://github.com/%s/pull/%d", repo, number), Title: title, Head: rows[at].Head, Review: row.Review,
			Change: row.Change, Plan: row.Plan, Risk: row.Risk, Blocks: row.Blocker,
		}},
		Options: []string{approveLabel, holdLabel}, Recommended: approveLabel, Waits: MergeWaits(number, rows[at].Head),
	}, true
}

// Waiting is the rows of the numbered PRs that wait on the owner's tap on this
// drive, under the repo its config names.
func Waiting(entry drive.Entry, numbers []int) (string, []Row, error) {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), io.Discard)
	if err != nil {
		return "", nil, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return "", nil, err
	}
	defer func() { _ = s.Close() }()
	rows, err := StoredRows(s, cfg.Repo, numbers)
	return cfg.Repo, rows, err
}

// MissingOf names the owner's lines a set of head-record words lacks.
func MissingOf(words map[string]string) []string {
	return Row{Words: words}.MissingWords()
}

func StoredRows(s *store.Store, repo string, numbers []int) ([]Row, error) {
	open, err := s.OpenRows(repo, string(machine.ActionTap), machine.MoverOwner, numbers)
	if err != nil {
		return nil, err
	}
	var out []Row
	for _, row := range open {
		_, exempt := row.Facts["exempt"]
		if (machine.Facts{Tap: machine.Tap(row.Facts[tapFact]), Exempt: exempt}).Tapped() || row.Facts[FactWithdrawn] != "" {
			continue
		}
		var missing []string
		if said := row.Facts[gates.MissingFact]; said != "" {
			if err := json.Unmarshal([]byte(said), &missing); err != nil {
				return nil, fmt.Errorf("#%d at %s: the gates its pass stored: %w", row.Number, short(row.Head), err)
			}
		}
		out = append(out, Row{row.Number, row.Head, row.Kind, row.Lane, missing, row.Facts})
	}
	return out, nil
}

func Holders(s *store.Store, numbers []int, held []SlackPR) ([]SlackPR, error) {
	prs, err := s.NumberedPRs(numbers)
	if err != nil {
		return nil, err
	}
	for _, pr := range prs {
		if found := (SlackPR{pr.Repo, pr.Number}); !slices.Contains(held, found) {
			held = append(held, found)
		}
	}
	return held, nil
}

func Respond(s *store.Store, repo string, hooks Hooks, rows []Row, number int, waits, label string, at time.Time) error {
	verdict, known := map[string]string{approveLabel: verdictApproved, mergeLabel: verdictApproved, holdLabel: verdictRejected}[label]
	if !known {
		return fmt.Errorf("a merge card answers %s or %s, not %q", approveLabel, holdLabel, label)
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
		Short: "Read {\"prs\": [...]}, {\"numbers\": [...]} or {\"answer\": {...}} on stdin: print the merge-row card for each PR that waits on the owner, every drive PR holding each number, or apply his Approve or Hold",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			var ask slackAsk
			if err := json.NewDecoder(c.InOrStdin()).Decode(&ask); err != nil {
				return fmt.Errorf("read the card request: %w", err)
			}
			live, err := cardDrives(c.Flag("drive").Value.String())
			if err != nil {
				return err
			}
			if k := ask.Answer; k != nil {
				at := slices.IndexFunc(live, func(entry drive.Entry) bool { return strings.EqualFold(entry.Repo, k.Repo) })
				if at < 0 {
					return fmt.Errorf("no live drive holds %s#%d", k.Repo, k.Number)
				}
				return withRows(c, live[at], []int{k.Number}, func(s *store.Store, repository string, rows []Row) error {
					return Respond(s, repository, postHooks(c.Context(), live[at].Bus(), lane, repository), rows, k.Number, k.Waits, k.Label, a.Now())
				})
			}
			if len(ask.Numbers) > 0 {
				held := []SlackPR{}
				for _, entry := range live {
					s, err := store.Open(entry.Store())
					if err != nil {
						return err
					}
					held, err = Holders(s, ask.Numbers, held)
					_ = s.Close()
					if err != nil {
						return err
					}
				}
				return json.NewEncoder(c.OutOrStdout()).Encode(map[string][]SlackPR{"prs": held})
			}
			cards := []SlackPRCard{}
			for _, entry := range live {
				held := slices.DeleteFunc(slices.Clone(ask.PRs), func(pr SlackPR) bool { return !strings.EqualFold(pr.Repo, entry.Repo) })
				if len(held) == 0 {
					continue
				}
				numbers := make([]int, len(held))
				for i, pr := range held {
					numbers[i] = pr.Number
				}
				if err := withRows(c, entry, numbers, func(_ *store.Store, repository string, rows []Row) error {
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

func cardDrives(name string) ([]drive.Entry, error) {
	if name != "" {
		entry, err := drive.Named(name)
		return []drive.Entry{entry}, err
	}
	live, err := drive.Live()
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(live, func(entry drive.Entry) bool {
		_, err := os.Stat(filepath.Join(entry.StateDir, configFile))
		return errors.Is(err, fs.ErrNotExist)
	}), nil
}

func withRows(c *cobra.Command, entry drive.Entry, numbers []int, use func(*store.Store, string, []Row) error) error {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), c.ErrOrStderr())
	if err != nil {
		return err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	rows, err := StoredRows(s, cfg.Repo, numbers)
	if err != nil {
		return err
	}
	return use(s, cfg.Repo, rows)
}
