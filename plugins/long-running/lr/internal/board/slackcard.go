package board

import (
	"crypto/sha256"
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
	Lands  string `json:"lands,omitempty"`
	Port   string `json:"port,omitempty"`

	Description string `json:"description,omitempty"`
}

type SlackPR struct {
	Repo   string `json:"repo"`
	Number int    `json:"number"`
}

type SlackShown struct {
	SlackPR
	Waits string `json:"waits"`
}

type slackAsk struct {
	PRs     []SlackPR    `json:"prs"`
	Numbers []int        `json:"numbers"`
	Shown   []SlackShown `json:"shown"`
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
	opening := ""
	if source, port := strings.CutPrefix(row.Port, prrow.PortOf); port {
		opening = "Port of #" + source + ": "
	}
	card := SlackCard{
		Status: "info", Title: fmt.Sprintf("%s#%d at %s", opening, row.Number, at),
		PRs: []SlackPRRow{{
			URL: fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number), Title: row.Title, Head: row.Head, Review: Standing(row),
			Change: prrow.ChangeWords(row), Plan: PlanWords(row), Risk: row.Risk, Blocks: prrow.BlocksWords(row), Port: portLine(repo, row), Description: row.Description,
		}},
	}
	if row.State == prrow.StateLanded {
		card.Status = "done"
	}
	if !offersApproval(row) {
		return card
	}
	card.Status, card.Title = "needs_you", fmt.Sprintf("%sApprove #%d at %s?", opening, row.Number, at)
	card.Options, card.Recommended, card.Waits = []string{approveLabel, holdLabel}, approveLabel, MergeWaits(row.Number, row.Head)
	if !row.ReadyForTap {
		card.PRs[0].Blocks, card.PRs[0].Lands = "", prrow.LandsOnce(row)
	}
	return card
}

func offersApproval(row store.ReviewRow) bool {
	return !row.Tapped && prrow.TapRefusal(row) == ""
}

func drawnSignature(card SlackCard) string {
	raw, err := json.Marshal(card)
	if err != nil {
		panic(err)
	}
	return fmt.Sprintf("%x", sha256.Sum256(raw))
}

func waitsOnHim(row store.ReviewRow) bool {
	return row.ReadyForTap && !row.Tapped
}

func portLine(repo string, row store.ReviewRow) string {
	source, port := strings.CutPrefix(row.Port, prrow.PortOf)
	if !port {
		return ""
	}
	return fmt.Sprintf("Port of [#%s](https://github.com/%s/pull/%s)", source, repo, source)
}

func portOpening(repo string, row store.ReviewRow) string {
	if line := portLine(repo, row); line != "" {
		return line + ". "
	}
	return ""
}

func FillPRChild(child map[string]any, row SlackPRRow) map[string]any {
	var summary []string
	if row.Port != "" {
		summary = append(summary, "**"+row.Port+".**")
	}
	for _, line := range []struct{ key, label, said string }{
		{"change", "What changes", row.Change}, {"plan", "Against the plan", row.Plan}, {"risk", "Risk", row.Risk}, {"blocker", "Blocks landing", row.Blocks},
	} {
		if line.said != "" {
			child[line.key] = line.said
			summary = append(summary, "**"+line.label+":** "+line.said)
		}
	}
	if row.Review != "" {
		summary = append(summary, "**Status:** "+row.Review)
	}
	if len(summary) > 0 {
		child["summary"] = strings.Join(summary, "\n\n")
	}
	if row.Head != "" {
		child["head"] = row.Head
	}
	if row.Title != "" {
		child["title"] = row.Title
	}
	return child
}

// PlanWords is the row's plan line and plan row said once, since a plan row
// opens with the line it checked.
func PlanWords(row store.ReviewRow) string {
	if strings.HasPrefix(row.PlanRow, row.PlanLine) {
		return row.PlanRow
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

func BugFixes(entry drive.Entry) (string, map[string][]store.ReviewRow, error) {
	cfg, err := config.Load(filepath.Join(entry.StateDir, configFile), io.Discard)
	if errors.Is(err, fs.ErrNotExist) {
		return "", nil, nil
	}
	if err != nil {
		return "", nil, err
	}
	s, err := store.Open(entry.Store())
	if err != nil {
		return "", nil, err
	}
	defer func() { _ = s.Close() }()
	rows, err := s.ReviewRows(cfg.Repo)
	if err != nil {
		return "", nil, err
	}
	fixes := map[string][]store.ReviewRow{}
	for _, row := range rows {
		if prrow.Terminal(row.State) || row.State == prrow.StateSuperseded || row.State == prrow.StateDraft || row.Tapped || prrow.LandsOnHisWord(row) {
			continue
		}
		for _, item := range prrow.Items(row.Description) {
			fixes[item] = append(fixes[item], row)
		}
	}
	return cfg.Repo, fixes, nil
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

func MarkShown(s *store.Store, repo string, rows []store.ReviewRow, shown []SlackShown, at time.Time) error {
	for _, card := range shown {
		found := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == card.Number })
		if found < 0 {
			continue
		}
		row, signature := rows[found], staleDrawn
		if now := Card(repo, rows[found]); now.Waits == card.Waits {
			signature = drawnSignature(now)
		}
		if _, err := s.PutFact(repo, card.Number, row.Head, FactCardDrawn, signature, at); err != nil {
			return err
		}
		if MergeWaits(card.Number, row.Head) != card.Waits {
			continue
		}
		if _, err := s.PutFact(repo, card.Number, row.Head, FactTapCard, tapCardInSlack, at); err != nil {
			return err
		}
	}
	return nil
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
		Short: "Read {\"prs\": [...]}, {\"numbers\": [...]}, {\"shown\": [...]} or {\"answer\": {...}} on stdin: print the card of each PR with a ledger row, its Approve and Hold only while it waits on the owner, every drive PR holding each number, record a card that now stands, or apply his Approve or Hold",
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
			if len(ask.Shown) > 0 {
				for _, entry := range live {
					shown := slices.DeleteFunc(slices.Clone(ask.Shown), func(card SlackShown) bool { return !strings.EqualFold(card.Repo, entry.Repo) })
					if len(shown) == 0 {
						continue
					}
					numbers := make([]int, len(shown))
					for i, card := range shown {
						numbers[i] = card.Number
					}
					if err := withRows(c, entry, numbers, ledgerRows, func(s *store.Store, repository string, rows []store.ReviewRow) error {
						return MarkShown(s, repository, rows, shown, a.Now())
					}); err != nil {
						return err
					}
				}
				return json.NewEncoder(c.OutOrStdout()).Encode(map[string]int{"shown": len(ask.Shown)})
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
				words := NewAnswerWords(a.Runner, entry.Checkout)
				if err := withRows(c, entry, numbers, ledgerRows, func(_ *store.Store, repository string, rows []store.ReviewRow) error {
					for _, pr := range held {
						at := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == pr.Number })
						if at < 0 {
							continue
						}
						card := Card(repository, rows[at])
						card.PRs[0].Plan = words.Said(c.Context(), card.PRs[0].Plan)
						cards = append(cards, SlackPRCard{pr, card})
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
