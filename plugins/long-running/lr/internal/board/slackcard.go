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
	"strconv"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
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

	Description string `json:"description,omitempty"`

	Row *store.ReviewRow `json:"row,omitempty"`
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

func Card(repo string, row store.ReviewRow) SlackCard {
	at := row.Head[:min(waitsSHA, len(row.Head))]
	card := SlackCard{
		Status: "info", Title: fmt.Sprintf("#%d at %s", row.Number, at),
		PRs: []SlackPRRow{{
			URL: fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number), Title: row.Title, Head: row.Head, Review: Standing(row),
			Change: row.ForUsers, Plan: PlanWords(row), Risk: row.Risk, Blocks: row.WaitsOn, Description: row.Description, Row: &row,
		}},
	}
	if row.State == prrow.StateLanded {
		card.Status = "done"
	}
	if waitsOnHim(row) {
		card.Status, card.Title = "needs_you", fmt.Sprintf("Approve #%d at %s?", row.Number, at)
		card.Options, card.Recommended, card.Waits = []string{approveLabel, holdLabel}, approveLabel, MergeWaits(row.Number, row.Head)
	}
	return card
}

func waitsOnHim(row store.ReviewRow) bool {
	return row.ReadyForTap && !row.Tapped
}

// PlanWords is the row's plan line and plan row, a sentence they share said once.
func PlanWords(row store.ReviewRow) string {
	if row.PlanLine == row.PlanRow {
		return row.PlanLine
	}
	return strings.TrimSpace(row.PlanLine + " " + row.PlanRow)
}

// Rows reads the numbered PRs' rows from the drive's PR ledger, under the repo
// its config names.
func Rows(entry drive.Entry, numbers []int) (string, []store.ReviewRow, error) {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), io.Discard)
	if err != nil {
		return "", nil, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return "", nil, err
	}
	defer func() { _ = s.Close() }()
	rows, err := ledgerRows(s, cfg.Repo, numbers)
	return cfg.Repo, rows, err
}

func Ports(entry drive.Entry, numbers []int) (string, map[int]int, error) {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), io.Discard)
	if err != nil {
		return "", nil, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return "", nil, err
	}
	defer func() { _ = s.Close() }()
	ports := map[int]int{}
	for _, number := range numbers {
		facts, err := s.PRFacts(cfg.Repo, number)
		if err != nil {
			return "", nil, err
		}
		if source, err := strconv.Atoi(facts[gates.PortOfAttr]); err == nil {
			ports[number] = source
		}
	}
	return cfg.Repo, ports, nil
}

func ledgerRows(s *store.Store, repo string, numbers []int) ([]store.ReviewRow, error) {
	all, err := s.ReviewRows(repo)
	return slices.DeleteFunc(all, func(row store.ReviewRow) bool { return !slices.Contains(numbers, row.Number) }), err
}

func Holders(s *store.Store, numbers []int, held []SlackPR) ([]SlackPR, error) {
	prs, err := s.NumberedReviewRows(numbers)
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

func Respond(s *store.Store, repo string, hooks Hooks, rows []store.ReviewRow, number int, waits, label string, at time.Time) error {
	verdict, known := map[string]string{approveLabel: verdictApproved, mergeLabel: verdictApproved, holdLabel: verdictRejected}[label]
	if !known {
		return fmt.Errorf("a merge card answers %s or %s, not %q", approveLabel, holdLabel, label)
	}
	found := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == number })
	if found < 0 {
		return refuseCard(hooks, number, waits, fmt.Sprintf("#%d has no row in the PR ledger", number))
	}
	if now := MergeWaits(number, rows[found].Head); now != waits {
		return refuseCard(hooks, number, waits, fmt.Sprintf("#%d moved to %s since the card asked %s; its new card asks again", number, short(rows[found].Head), waits))
	}
	return Answer(s, repo, hooks, rows[found], verdict, at)
}

func refuseCard(hooks Hooks, number int, waits, reason string) error {
	cardHead := waits[strings.LastIndex(waits, ":")+1:]
	if err := hooks.Refuse(TapRecord{number, cardHead, byOwner, machine.TapTapped}, reason); err != nil {
		return err
	}
	return errors.New(reason)
}

func slackCardCommand(a *app.App) *cobra.Command {
	var lane string
	cmd := &cobra.Command{
		Use:   "card",
		Short: "Read {\"prs\": [...]}, {\"numbers\": [...]} or {\"answer\": {...}} on stdin: print the card of each PR with a ledger row, its Approve and Hold only while it waits on the owner, every drive PR holding each number, or apply his Approve or Hold",
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
				return withRows(c, live[at], []int{k.Number}, ledgerRows, func(s *store.Store, repository string, rows []store.ReviewRow) error {
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
				if err := withRows(c, entry, numbers, ledgerRows, func(_ *store.Store, repository string, rows []store.ReviewRow) error {
					for _, pr := range held {
						if at := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == pr.Number }); at >= 0 {
							cards = append(cards, SlackPRCard{pr, Card(repository, rows[at])})
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

func withRows(c *cobra.Command, entry drive.Entry, numbers []int, read func(*store.Store, string, []int) ([]store.ReviewRow, error), use func(*store.Store, string, []store.ReviewRow) error) error {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), c.ErrOrStderr())
	if err != nil {
		return err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return err
	}
	defer func() { _ = s.Close() }()
	rows, err := read(s, cfg.Repo, numbers)
	if err != nil {
		return err
	}
	return use(s, cfg.Repo, rows)
}
