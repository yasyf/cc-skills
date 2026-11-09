package board

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	approveLabel   = "Approve"
	mergeLabel     = "Merge"
	holdLabel      = "Hold"
	withdrawnLead  = "Withdrawn: "
	supersededLead = "Superseded: "
)

var prScope = regexp.MustCompile(`^[\w./-]+(?:\([\w./-]+\))?!?:\s+`)

type SlackCard struct {
	Status      string       `json:"status"`
	Title       string       `json:"title"`
	PRs         []SlackPRRow `json:"prs"`
	Options     []string     `json:"options"`
	Recommended string       `json:"recommended"`
	Waits       string       `json:"waits"`
	MergesAt    string       `json:"merges_at,omitempty"`
}

type SlackPRRow struct {
	URL    string `json:"url"`
	Title  string `json:"title,omitempty"`
	Head   string `json:"head,omitempty"`
	Review string `json:"review,omitempty"`
	Change string `json:"change"`
	Plan   string `json:"plan"`
	ELI5   string `json:"eli5"`
	ShowMe string `json:"show_me"`
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

type SlackRefused struct {
	SlackPR
	Reason string `json:"reason"`
}

type SlackUnheard struct {
	Card      string `json:"card"`
	Label     string `json:"label"`
	Question  string `json:"question"`
	Other     bool   `json:"other"`
	Permalink string `json:"permalink"`
	Key       string `json:"key"`
}

type slackAsk struct {
	PRs     []SlackPR      `json:"prs"`
	Numbers []int          `json:"numbers"`
	Shown   []SlackShown   `json:"shown"`
	Refused []SlackRefused `json:"refused"`
	Answer  *struct {
		SlackPR
		Waits string `json:"waits"`
		Label string `json:"label"`
	} `json:"answer"`
	Unheard *SlackUnheard `json:"unheard"`
}

type SlackPRCard struct {
	SlackPR
	Card SlackCard `json:"card"`
}

func Card(repo string, row store.ReviewRow) SlackCard {
	opening, lead := portTitleOpening(row), ""
	card := SlackCard{
		Status: "info",
		PRs: []SlackPRRow{{
			URL: fmt.Sprintf("https://github.com/%s/pull/%d", repo, row.Number), Title: row.Title, Head: row.Head, Review: Standing(row),
			Change: prrow.ChangeWords(row), Plan: planDeparture(row), ELI5: prrow.ELI5Words(row), ShowMe: prrow.ShowMe(row), Blocks: prrow.BlocksWords(row), Port: portLine(repo, row), Description: row.Description,
		}},
	}
	switch row.State {
	case prrow.StateLanded:
		card.Status = "done"
	case prrow.StateSuperseded:
		why := strings.TrimSuffix(row.SupersededBy, ".") + "."
		said := withdrawnLead + why
		lead = withdrawnLead
		if strings.HasPrefix(why, supersededLead) {
			lead, said = supersededLead, why
		}
		card.PRs[0].Blocks = said
	}
	if !OffersApproval(row) {
		card.Title, card.PRs[0].Head = headed(row.Number, row.Title, func(named string) string { return opening + lead + named }), ""
		return card
	}
	card.Status, card.Title = "needs_you", headed(row.Number, row.Title, func(named string) string { return fmt.Sprintf("%sApprove %s?", opening, named) })
	card.Options, card.Recommended, card.Waits = []string{approveLabel, holdLabel}, approveLabel, MergeWaits(row.Number, row.Head)
	if merges, err := store.Parse(row.MergesAt); err == nil {
		card.Title = headed(row.Number, row.Title, func(named string) string {
			return fmt.Sprintf("%sApprove %s before it merges itself at %s?", opening, named, pacific(merges))
		})
		card.MergesAt = row.MergesAt
	}
	if !row.ReadyForTap {
		card.PRs[0].Blocks, card.PRs[0].Lands = "", prrow.LandsOnce(row)
	}
	return card
}

func headed(number int, title string, frame func(named string) string) string {
	if framed := frame(namedTitle(number, title)); utf8.RuneCountInString(framed) <= slackTitleChars {
		return framed
	}
	return frame(fmt.Sprintf("#%d", number))
}

func namedTitle(number int, title string) string {
	words := PlainTitle(title)
	if words == "" {
		return fmt.Sprintf("#%d", number)
	}
	return fmt.Sprintf("#%d, %s", number, words)
}

// PlainTitle is a PR title without its component scope and gitmoji.
func PlainTitle(title string) string {
	return strings.TrimLeftFunc(prScope.ReplaceAllString(title, ""), func(r rune) bool {
		return unicode.IsSymbol(r) || unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Cf, r) || unicode.IsSpace(r)
	})
}

func OffersApproval(row store.ReviewRow) bool {
	return !row.Tapped && row.State != prrow.StateExempt && prrow.TapRefusal(row) == "" && prrow.WordsWait(row) == ""
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

func portTitleOpening(row store.ReviewRow) string {
	source, port := strings.CutPrefix(row.Port, prrow.PortOf)
	if !port {
		return ""
	}
	return cardtext.PortOpener + "#" + source + ": "
}

func portLine(repo string, row store.ReviewRow) string {
	source, port := strings.CutPrefix(row.Port, prrow.PortOf)
	if !port {
		return ""
	}
	return fmt.Sprintf("%s[#%s](https://github.com/%s/pull/%s)", cardtext.PortOpener, source, repo, source)
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
		{"change", "What changes", row.Change}, {"plan", "Against the plan", row.Plan}, {"eli5", "ELI5", row.ELI5}, {"show_me", "Show me", row.ShowMe}, {"blocker", "Blocks landing", row.Blocks},
	} {
		if line.said != "" {
			child[line.key] = line.said
			sep := " "
			if strings.HasPrefix(line.said, "- ") {
				sep = "\n"
			}
			summary = append(summary, "**"+line.label+":**"+sep+line.said)
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
// opens with the line it checked, or that its PR body names no plan step.
const slackTitleChars = 140

const planOwed = "Not written yet: its PR body names no plan step."

func planDeparture(row store.ReviewRow) string {
	if !prrow.DepartsFromPlan(row) {
		return ""
	}
	return PlanWords(row)
}

func PlanWords(row store.ReviewRow) string {
	if strings.HasPrefix(row.PlanRow, row.PlanLine) {
		return cmp.Or(row.PlanRow, planOwed)
	}
	return strings.TrimSpace(row.PlanLine + " " + row.PlanRow)
}

func DriveConfig(entry drive.Entry) (config.Config, error) {
	return config.Load(filepath.Join(entry.StateDir, configFile), io.Discard)
}

// Rows reads the numbered PRs' rows from the drive's PR ledger, under the repo
// its config names.
func Rows(entry drive.Entry, numbers []int) (string, []store.ReviewRow, error) {
	cfg, err := DriveConfig(entry)
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
		if prrow.Terminal(row.State) || row.State == prrow.StateSuperseded || row.State == prrow.StateDraft || row.State == prrow.StateExempt || row.Tapped || prrow.LandsOnHisWord(row) {
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
	if verdict == verdictApproved && MergeWaits(number, rows[found].Head) != waits {
		return tapShownHead(s, repo, hooks, number, cardHead(waits), at)
	}
	return Answer(s, repo, hooks, rows[found], verdict, at)
}

func tapShownHead(s *store.Store, repo string, hooks Hooks, number int, shown string, at time.Time) error {
	head, err := s.FullHead(repo, number, shown)
	if err != nil {
		return err
	}
	if err := prrow.Tap(s, repo, number, head, byOwner, at); err != nil {
		return err
	}
	return hooks.Tap(TapRecord{number, head, byOwner, machine.TapTapped})
}

func cardHead(waits string) string {
	return waits[strings.LastIndex(waits, ":")+1:]
}

func MarkShown(s *store.Store, repo string, rows []store.ReviewRow, shown []SlackShown, at time.Time) error {
	for _, card := range shown {
		found := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == card.Number })
		if found < 0 || MergeWaits(card.Number, rows[found].Head) != card.Waits {
			if err := markShownAtCardHead(s, repo, card, at); err != nil {
				return err
			}
		}
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
		stored, err := s.Facts(repo, card.Number, row.Head)
		if err != nil {
			return err
		}
		if stored[gates.ShownFact] != "" {
			continue
		}
		if _, err := s.PutFact(repo, card.Number, row.Head, gates.ShownFact, store.Stamp(at), at); err != nil {
			return err
		}
	}
	return nil
}

func MarkRefused(s *store.Store, repo string, rows []store.ReviewRow, refused []SlackRefused, at time.Time) error {
	for _, card := range refused {
		found := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == card.Number })
		if found < 0 {
			continue
		}
		row := rows[found]
		if _, err := s.PutFact(repo, card.Number, row.Head, FactCardRefused, drawnSignature(Card(repo, row))+" "+card.Reason, at); err != nil {
			return err
		}
	}
	return nil
}

func markShownAtCardHead(s *store.Store, repo string, card SlackShown, at time.Time) error {
	named := mergeCard.FindStringSubmatch(card.Waits)
	if named == nil || named[1] != strconv.Itoa(card.Number) {
		return nil
	}
	stored, err := s.Facts(repo, card.Number, named[2])
	if err != nil || stored[gates.ShownFact] != "" {
		return err
	}
	_, err = s.PutFact(repo, card.Number, named[2], gates.ShownFact, store.Stamp(at), at)
	return err
}

func refuseCard(hooks Hooks, number int, waits, reason string) error {
	if err := hooks.Refuse(TapRecord{number, cardHead(waits), byOwner, machine.TapTapped}, reason); err != nil {
		return err
	}
	return errors.New(reason)
}

func slackCardCommand(a *app.App) *cobra.Command {
	var lane string
	cmd := &cobra.Command{
		Use:   "card",
		Short: "Read {\"prs\": [...]}, {\"numbers\": [...]}, {\"shown\": [...]}, {\"refused\": [...]}, {\"answer\": {...}} or {\"unheard\": {...}} on stdin: print the card of each PR with a ledger row, its Approve and Hold only while it waits on the owner, every drive PR holding each number, record a card that now stands or one Slack refused to redraw, or apply his Approve or Hold",
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
			if u := ask.Unheard; u != nil {
				if err := tellUnheard(a, c, live, lane, *u); err != nil {
					return err
				}
				return json.NewEncoder(c.OutOrStdout()).Encode(map[string]bool{"heard": true})
			}
			if k := ask.Answer; k != nil {
				at := slices.IndexFunc(live, func(entry drive.Entry) bool { return strings.EqualFold(entry.Repo, k.Repo) })
				if at < 0 {
					return fmt.Errorf("no live drive holds %s#%d", k.Repo, k.Number)
				}
				return withRows(c, live[at], []int{k.Number}, ledgerRows, func(s *store.Store, repository string, rows []store.ReviewRow) error {
					return Respond(s, repository, postHooks(c.Context(), cardAnswers{a.Runner, live[at].Checkout, c.ErrOrStderr()}, live[at].Bus(), lane, repository), rows, k.Number, k.Waits, k.Label, a.Now())
				})
			}
			if len(ask.Refused) > 0 {
				for _, entry := range live {
					refused := slices.DeleteFunc(slices.Clone(ask.Refused), func(card SlackRefused) bool { return !strings.EqualFold(card.Repo, entry.Repo) })
					if len(refused) == 0 {
						continue
					}
					numbers := make([]int, len(refused))
					for i, card := range refused {
						numbers[i] = card.Number
					}
					if err := withRows(c, entry, numbers, ledgerRows, func(s *store.Store, repository string, rows []store.ReviewRow) error {
						return MarkRefused(s, repository, rows, refused, a.Now())
					}); err != nil {
						return err
					}
				}
				return json.NewEncoder(c.OutOrStdout()).Encode(map[string]int{"refused": len(ask.Refused)})
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
					explained, err := prrow.ReadELI5(c.Context(), a.Runner, entry.Checkout, repository, numbers)
					if err != nil {
						fmt.Fprintf(c.ErrOrStderr(), "PR cards draw each ELI5 from its PR body's Context, since the reviewer bot's could not be read: %v\n", err)
					}
					for _, pr := range held {
						at := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == pr.Number })
						if at < 0 {
							continue
						}
						plain := func(text string) string {
							return TestWords(c.Context(), a.Runner, ccx.CLI{Runner: a.Runner}, entry.Checkout, repository, pr.Number, rows[at].Head, text)
						}
						rows[at].ELI5, rows[at].Title = explained[pr.Number], plain(rows[at].Title)
						card := Card(repository, rows[at])
						card.PRs[0].Plan, card.PRs[0].Change = words.Said(c.Context(), card.PRs[0].Plan), plain(card.PRs[0].Change)
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

func tellUnheard(a *app.App, c *cobra.Command, live []drive.Entry, lane string, u SlackUnheard) error {
	if u.Card == "" || u.Key == "" {
		return fmt.Errorf("an unheard answer needs its card and key, got %+v", u)
	}
	if len(live) == 0 {
		return fmt.Errorf("no live drive to tell of the answer on %s", u.Card)
	}
	wakers := make([]Waker, len(live))
	for i, entry := range live {
		s, err := store.Open(entry.Store())
		if err != nil {
			return err
		}
		defer func() { _ = s.Close() }()
		wakers[i] = wakerFor(a, c, CardsOf(a, entry, entry.Root(), s), lane)
	}
	return unheardOnDrive(c.Context(), wakers, u)
}

func unheardOnDrive(ctx context.Context, wakers []Waker, u SlackUnheard) error {
	for _, w := range wakers {
		_, rows, err := w.Cards.rows(ctx)
		if err != nil {
			return err
		}
		asker, err := w.asker(ctx, CardAnswer{Card: u.Card}, rows)
		if err != nil {
			return err
		}
		if asker != "" || rows[u.Card][sendsField] != "" {
			return w.Unheard(ctx, u)
		}
	}
	return wakers[0].Unheard(ctx, u)
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
