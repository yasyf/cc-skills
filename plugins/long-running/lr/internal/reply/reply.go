package reply

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/cardtext"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/explain"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talkboard"
)

const (
	communicator = "owner-relay"
	storeDir     = "replies"
	postTimeout  = 30 * time.Second
	maxTitle     = 140
	maxPick      = 61
	maxPicks     = 4
	maxLinks     = 5
	maxPRs       = 10
	maxBlocks    = 8
	cardPrefix   = "card-"
	withdrawn    = " withdrawn"
	alreadyGone  = "no block "
	doneLead     = "Done, nothing for you"
	pickLead     = "Tap one below"
	needsYou     = "needs_you"
	progress     = "progress"
	info         = "info"
	incident     = "incident"
	done         = "done"

	typeExplanation = "explanation"
	typeUpdate      = "update"
	typeForYou      = "for_you"
	typeQuestion    = "question"

	threadIndex = "thread index"

	diagramNote = "The diagram is on the Chat page of your dashboard."
)

var (
	statuses   = []string{info, progress, needsYou, incident, done}
	types      = []string{typeExplanation, typeUpdate, typeForYou, typeQuestion, incident, done}
	orderRow   = regexp.MustCompile(`^m\d+(?:\.\d+)?$`)
	replyID    = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,47}$`)
	pullURL    = regexp.MustCompile(`^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$`)
	hiddenCite = regexp.MustCompile(`(?s)\s*<!--.*?-->`)
	slackSent  = regexp.MustCompile(`(?m)^sent: \S+ ts=\d{10}\.\d{6} (\S+)`)
	mdLink     = regexp.MustCompile(`\[([^\]\n]+)\]\((https?://[^\s)]+)\)`)
	notebook   = regexp.MustCompile(`https://[\w.-]*datadoghq\.com/notebook/\d+`)
	slackStamp = regexp.MustCompile(`/p(\d{10})(\d{6})(?:\?|$)`)
	namedPR    = regexp.MustCompile(`(?i)github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)|([\w.-]+)#(\d+)\b|(?:^|[^\w/#-])#(\d+)\b|\bPRs? #?(\d+)\b`)
)

type Link struct {
	Label string `json:"label"`
	URL   string `json:"url"`
}

type Build struct {
	Repo   string `json:"repo,omitempty"`
	Branch string `json:"branch,omitempty"`
	None   string `json:"none,omitempty"`
}

// Lines are a PR's four plain lines, read from its row in the PR ledger.
type Lines struct {
	Change string `json:"change,omitempty"`
	Plan   string `json:"plan,omitempty"`
	Risk   string `json:"risk,omitempty"`
	Blocks string `json:"blocks,omitempty"`
}

// prCard is one PR a pick card draws, with its lines read from the PR ledger.
type prCard struct {
	URL    string `json:"url"`
	Head   string `json:"head,omitempty"`
	Title  string `json:"title,omitempty"`
	Review string `json:"review,omitempty"`
	Lines
	Row *store.ReviewRow `json:"row,omitempty"`
}

type Reply struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	MD          string   `json:"md"`
	Status      string   `json:"status,omitempty"`
	Type        string   `json:"type,omitempty"`
	Picks       []string `json:"picks,omitempty"`
	Recommended string   `json:"recommended,omitempty"`
	Build       *Build   `json:"build,omitempty"`
	Links       []Link   `json:"links,omitempty"`
	PRs         []string `json:"prs,omitempty"`
	PRCards     []prCard `json:"pr_cards,omitempty"`
	OpenPicks   []string `json:"-"`
	Thread      string   `json:"thread,omitempty"`
	Order       string   `json:"order,omitempty"`
	Unprompted  bool     `json:"unprompted,omitempty"`
	Action      string   `json:"action,omitempty"`
	By          string   `json:"by,omitempty"`
	Routed      int      `json:"routed,omitempty"`

	Blocks  []json.RawMessage `json:"blocks,omitempty"`
	Diagram json.RawMessage   `json:"diagram,omitempty"`
	On      string            `json:"on,omitempty"`
}

type Outcome struct {
	Surface string `json:"surface"`
	Ref     string `json:"ref,omitempty"`
	Error   string `json:"error,omitempty"`
}

type delivery struct {
	Reply    Reply     `json:"reply"`
	At       time.Time `json:"at"`
	Re       string    `json:"re,omitempty"`
	Surfaces []Outcome `json:"surfaces"`
}

type Surface func(ctx context.Context, r Reply, sent []Outcome) []Outcome

type step struct {
	surface Surface
	reply   Reply
}

type Fanout struct {
	Runner shell.Runner
	Entry  drive.Entry
	Now    func() time.Time
	Judge  board.Judge
}

func (f Fanout) Send(ctx context.Context, r Reply) []Outcome {
	r = f.aliased(r).typed()
	seen := r
	seen.MD = hiddenCite.ReplaceAllString(r.MD, "")
	told := seen.diagramNoted()
	steps := []step{{f.board, told}, {f.thread, told}, {f.slack, told}, {f.cardCopies, told}, {f.record, r}, {f.dashboard, seen}, {f.ledger, r}}
	switch {
	case r.quiet():
		steps = []step{{f.board, told}, {f.slack, told}, {f.cardCopies, told}, {f.record, r}, {f.dashboard, seen}, {f.ledger, r}}
	case r.Order == "" || r.Status != info || len(r.Picks) > 0:
		steps = steps[:len(steps)-1]
	}
	sent := make([]Outcome, 0, 6)
	for _, s := range steps {
		sent = append(sent, s.surface(ctx, s.reply, sent)...)
	}
	return append(sent, f.indexThreads(r, sent)...)
}

func (f Fanout) indexThreads(r Reply, sent []Outcome) []Outcome {
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		return []Outcome{{Surface: threadIndex, Error: err.Error()}}
	}
	defer func() { _ = s.Close() }()
	var missed []Outcome
	for _, o := range sent {
		if !strings.HasPrefix(o.Surface, "slack") || owner.ThreadRoot(o.Ref) == "" {
			continue
		}
		if err := s.PutSlackThread(owner.ReplyThread(o.Ref, r.ID, r.Order, r.Title, r.Picks)); err != nil {
			missed = append(missed, Outcome{Surface: threadIndex, Error: err.Error()})
		}
	}
	return missed
}

// withCardLinks links each open card the reply names to the Slack message he
// taps it on, longest id first so no card's name is taken for a shorter one's.
// The reply posts whatever it finds: the second result is one warning line when
// the board did not read, or empty.
func (f Fanout) withCardLinks(ctx context.Context, r Reply) (Reply, string) {
	outcomes, err := board.ReadOutcomes(ctx, f.Runner, f.Entry.Root())
	if err != nil {
		return r, fmt.Sprintf("the board did not read, so no card this names carries its Slack link: %v", err)
	}
	cards := slices.DeleteFunc(outcomes.OpenCards(), func(card board.OpenCard) bool {
		return card.ID == cardPrefix+r.ID || linkedCard(r.MD, card) == r.MD && !strings.Contains(r.MD, card.URL)
	})
	slices.SortFunc(cards, func(one, other board.OpenCard) int { return cmp.Compare(len(other.ID), len(one.ID)) })
	checking, stop := context.WithTimeout(ctx, postTimeout)
	defer stop()
	settled, checked := board.Cards{Runner: f.Runner, Checkout: f.Entry.Checkout}.SettledByHim(checking, f.Judge, cards)
	var warned []string
	if checked != nil {
		warned = append(warned, fmt.Sprintf("his answers did not read for every card this names, so one he settled may still be linked: %v", checked))
	}
	for _, card := range cards {
		if found := settled[card.ID]; found != nil {
			warned = append(warned, fmt.Sprintf("%s is not linked because %s; take the words off this reply and run `lr board cards sweep` to take it down", card.ID, found.Line()))
			continue
		}
		r.MD = linkedCard(r.MD, card)
		r.OpenPicks = append(r.OpenPicks, card.URL)
	}
	return r, strings.Join(warned, "; ")
}

func (r Reply) namedPRs() ([][]string, []int) {
	said := namedPR.FindAllStringSubmatch(r.Title+"\n"+r.MD+"\n"+strings.Join(r.PRs, "\n"), -1)
	numbers := make([]int, 0, len(said))
	for _, found := range said {
		number, _ := strconv.Atoi(cmp.Or(found[2], found[4], found[5], found[6]))
		numbers = append(numbers, number)
	}
	return said, numbers
}

func inRepo(repo string, found []string) bool {
	return strings.HasSuffix(strings.ToLower(repo), strings.ToLower(cmp.Or(found[1], found[3], repo)))
}

func (f Fanout) withPortOpening(r Reply) (Reply, string) {
	said, numbers := r.namedPRs()
	if len(numbers) == 0 {
		return r, ""
	}
	repo, ports, err := board.Ports(f.Entry, numbers)
	if err != nil {
		return r, fmt.Sprintf("the PR ledger did not read, so no port this names opens the reply with port of #n: %v", err)
	}
	var opening []string
	for n, found := range said {
		source, port := ports[numbers[n]]
		if !port || !inRepo(repo, found) {
			continue
		}
		line := fmt.Sprintf("port of [#%d](https://github.com/%s/pull/%d)", source, repo, source)
		opens := regexp.MustCompile(fmt.Sprintf(`(?i)^\s*port of \[?#%d\b`, source))
		if !slices.Contains(opening, line) && !opens.MatchString(r.Title) && !opens.MatchString(r.MD) {
			opening = append(opening, line)
		}
	}
	if len(opening) > 0 {
		r.MD = strings.Join(opening, ", ") + "\n\n" + r.MD
	}
	return r, ""
}

func (f Fanout) tapWarning(r Reply) string {
	said, numbers := r.namedPRs()
	if len(numbers) == 0 {
		return ""
	}
	repo, rows, err := board.Rows(f.Entry, numbers)
	if err != nil {
		return fmt.Sprintf("the PR ledger did not read, so no PR this names was checked for a tap he owes: %v", err)
	}
	var missing, tapped, standing []string
	for n, found := range said {
		at := slices.IndexFunc(rows, func(row store.ReviewRow) bool { return row.Number == numbers[n] })
		if at < 0 || !inRepo(repo, found) {
			continue
		}
		url := fmt.Sprintf("https://github.com/%s/pull/%d", repo, numbers[n])
		listed := func(pr string) bool { return strings.EqualFold(pr, url) }
		switch {
		case rows[at].Tapped && len(r.Picks) > 0 && !slices.Contains(tapped, url):
			tapped = append(tapped, url)
		case prrow.LandsOnHisWord(rows[at]) && len(r.Picks) > 0 && !slices.Contains(standing, url):
			standing = append(standing, url)
		case rows[at].ReadyForTap && !rows[at].Tapped && !slices.ContainsFunc(r.PRs, listed) && !slices.Contains(missing, url):
			missing = append(missing, url)
		}
	}
	var warned []string
	if len(tapped) > 0 {
		warned = append(warned, "this asks him to pick on a PR he already tapped, so take it off the question: "+strings.Join(tapped, ", "))
	}
	if len(standing) > 0 {
		warned = append(warned, "this asks him to pick on a PR that lands on his standing word with no tap, so take it off the question: "+strings.Join(standing, ", "))
	}
	if len(missing) > 0 {
		warned = append(warned, "this names a PR waiting on his tap and does not list it, so it carries no tap card; edit the reply with it in prs: "+strings.Join(missing, ", "))
	}
	return strings.Join(warned, "; ")
}

func linkedCard(md string, card board.OpenCard) string {
	if strings.Contains(md, card.URL) {
		return md
	}
	id := regexp.QuoteMeta(card.ID)
	words := strings.ReplaceAll(regexp.QuoteMeta(strings.TrimPrefix(card.ID, cardPrefix)), "-", `[ -]`)
	for _, name := range []struct{ pattern, label string }{
		{"`?" + `\b` + id + `\b` + "`?", card.Title},
		{`(?i)\bcard ` + words + `\b|\b` + words + ` card\b`, ""},
		{`(?i)` + regexp.QuoteMeta(card.Title), ""},
	} {
		found := regexp.MustCompile(name.pattern).FindStringIndex(md)
		if found != nil {
			return md[:found[0]] + "[" + cmp.Or(name.label, md[found[0]:found[1]]) + "](" + card.URL + ")" + md[found[1]:]
		}
	}
	return md
}

func (f Fanout) aliased(r Reply) Reply {
	s, err := store.Open(f.Entry.Store())
	if err != nil {
		return r
	}
	defer func() { _ = s.Close() }()
	names, err := s.Names()
	if err != nil {
		return r
	}
	aliases := lanes.AliasesOf(names)
	r.Title, r.MD, r.Action = aliases.In(r.Title), aliases.In(r.MD), aliases.In(r.Action)
	picks := aliases.AllIn(r.Picks)
	if len(slices.Compact(slices.Sorted(slices.Values(picks)))) == len(picks) {
		r.Picks, r.Recommended = picks, aliases.In(r.Recommended)
	}
	r.PRCards = slices.Clone(r.PRCards)
	for n, card := range r.PRCards {
		r.PRCards[n].Lines = Lines{aliases.In(card.Change), aliases.In(card.Plan), aliases.In(card.Risk), aliases.In(card.Blocks)}
	}
	r.Blocks = slices.Clone(r.Blocks)
	for n, block := range r.Blocks {
		r.Blocks[n] = json.RawMessage(aliases.In(string(block)))
	}
	return r
}

func (r Reply) typed() Reply {
	switch {
	case r.Type != "":
	case len(r.Picks) > 0:
		r.Type = typeQuestion
	case r.Status == needsYou:
		r.Type = typeForYou
	case r.Status == progress:
		r.Type = typeUpdate
	case r.Status == incident, r.Status == done:
		r.Type = r.Status
	default:
		r.Type = typeExplanation
	}
	return r
}

func (r Reply) diagramNoted() Reply {
	if len(r.Diagram) > 0 {
		r.MD += "\n\n" + diagramNote
	}
	return r
}

func (r Reply) quiet() bool {
	return r.Order != "" && len(r.Picks) == 0 && r.Status == progress
}

func (r Reply) led() Reply {
	if r.Order == "" || strings.TrimSpace(r.Title) == "" || strings.TrimSpace(r.MD) == "" {
		return r
	}
	lead := strings.TrimRight(strings.TrimSpace(r.Action), ".")
	switch {
	case lead != "":
		r.Status = cmp.Or(r.Status, needsYou)
	case len(r.Picks) > 0:
		lead = pickLead
		r.Action = lead
		r.Status = cmp.Or(r.Status, needsYou)
	case r.Status == progress:
		return r
	default:
		lead = doneLead
	}
	if !strings.HasPrefix(r.Title, lead) {
		r.Title = shortened(lead+": "+r.Title, maxTitle)
	}
	if md := strings.TrimSpace(r.MD); !strings.HasPrefix(md, lead) {
		r.MD = lead + ".\n\n" + md
	}
	return r
}

func (r Reply) mended() (Reply, []string) {
	var warned []string
	warn := func(format string, args ...any) { warned = append(warned, fmt.Sprintf(format, args...)) }
	if r.Order != "" && !orderRow.MatchString(r.Order) {
		warn("order %q is not a ledger row such as m347, so it marks nothing", r.Order)
		r.Order = ""
	}
	if r.Action != "" && r.Order == "" {
		warn("action names what he must do about an order, and this reply names no order, so it was left off")
		r.Action = ""
	}
	r.Action = strings.Join(strings.Fields(r.Action), " ")
	if (r.By != "" || r.Routed != 0) && !r.quiet() {
		warn("by and routed mark his ledger row only from a progress reply to an order with no picks, so they were left off")
		r.By, r.Routed = "", 0
	}
	if !replyID.MatchString(r.ID) {
		if r.ID != "" {
			warn("id %q is not one an earlier reply returned, so this is a new reply", r.ID)
		}
		r.ID = ""
	}
	r.MD = cmp.Or(strings.TrimSpace(r.MD), strings.TrimSpace(r.Title))
	r.Title = shortened(cmp.Or(strings.Join(strings.Fields(r.Title), " "), firstLine(r.MD)), maxTitle)
	if r.Status != "" && !slices.Contains(statuses, r.Status) {
		warn("status %q is not one of %s, so it was left off", r.Status, strings.Join(statuses, ", "))
		r.Status = ""
	}
	if r.Type != "" && !slices.Contains(types, r.Type) {
		warn("type %q is not one of %s, so it was left off", r.Type, strings.Join(types, ", "))
		r.Type = ""
	}
	if len(r.Diagram) > 0 {
		if problems := explain.ArchProblems(r.Diagram); len(problems) > 0 {
			warn("the diagram does not draw, so it was left off: %s", strings.Join(problems, "; "))
			r.Diagram = nil
		}
	}
	r, warned = r.mendedPicks(warned)
	r.Links = kept(r.Links, maxLinks, "links", &warned, func(link Link) bool {
		return link.Label != "" && (strings.HasPrefix(link.URL, "https://") || strings.HasPrefix(link.URL, "http://"))
	})
	r.PRs = kept(r.PRs, maxPRs, "PRs", &warned, pullURL.MatchString)
	r.Blocks = kept(r.Blocks, maxBlocks, "blocks", &warned, func(raw json.RawMessage) bool {
		var block struct {
			Type string `json:"type"`
		}
		return json.Unmarshal(raw, &block) == nil && block.Type != ""
	})
	if notebook.MatchString(mdLink.ReplaceAllString(r.MD, "$1")) {
		warn("a notebook link in md shows no title, and Slack draws none, so write it as [its title](url)")
	}
	return r, warned
}

func (r Reply) mendedPicks(warned []string) (Reply, []string) {
	var picks []string
	for _, pick := range r.Picks {
		pick = shortened(strings.TrimSpace(pick), maxPick)
		if pick != "" && !slices.Contains(picks, pick) {
			picks = append(picks, pick)
		}
	}
	if len(picks) > maxPicks {
		warned = append(warned, fmt.Sprintf("a card offers at most %d picks, so the rest were left off", maxPicks))
		picks = picks[:maxPicks]
	}
	if len(picks) == 1 {
		warned = append(warned, fmt.Sprintf("one pick is no choice, so %q was left off", picks[0]))
		picks = nil
	}
	r.Picks = picks
	r.Recommended = shortened(r.Recommended, maxPick)
	if len(picks) > 0 && !slices.Contains(picks, r.Recommended) {
		warned = append(warned, "no pick is recommended")
		r.Recommended = ""
	}
	if len(picks) == 0 {
		r.Recommended = ""
	}
	if r.Build != nil && r.Recommended == "" {
		warned = append(warned, "build names what the recommended pick is built on, and nothing is recommended, so it was left off")
		r.Build = nil
	}
	return r, warned
}

func kept[T any](items []T, limit int, name string, warned *[]string, valid func(T) bool) []T {
	good := slices.DeleteFunc(slices.Clone(items), func(item T) bool { return !valid(item) })
	if dropped := len(items) - len(good); dropped > 0 {
		*warned = append(*warned, fmt.Sprintf("%d of the %s are malformed, so they were left off", dropped, name))
	}
	if len(good) > limit {
		*warned = append(*warned, fmt.Sprintf("a reply carries at most %d %s, so the rest were left off", limit, name))
		good = good[:limit]
	}
	return good
}

func shortened(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	cut := string(runes[:limit-1])
	if space := strings.LastIndexByte(cut, ' '); space > 0 {
		cut = cut[:space]
	}
	return cut + "…"
}

func firstLine(md string) string {
	line, _, _ := strings.Cut(md, "\n")
	return strings.TrimSpace(line)
}

func (r Reply) bareExplainMore() error {
	card, err := json.Marshal(map[string]any{"type": "card", "summary": strings.TrimPrefix(strings.TrimSpace(r.MD), strings.TrimRight(r.Action, ".")+"."), "children": r.Blocks})
	if err != nil {
		return err
	}
	if name := cardtext.Bare(card); name != "" {
		return cardtext.Refusal(name)
	}
	return nil
}

// withPRCards reads each PR the reply names from the drive's PR ledger, for
// its card to carry. The reply posts whatever it finds: the second result is
// one warning line naming what is missing, or empty. A PR that does not wait on
// his tap, on a reply with no picks, is only a mention.
func (f Fanout) withPRCards(r Reply) (Reply, string) {
	if len(r.PRs) == 0 {
		return r, ""
	}
	numbers := make([]int, len(r.PRs))
	for n, pr := range r.PRs {
		numbers[n], _ = strconv.Atoi(pullURL.FindStringSubmatch(pr)[2])
	}
	repo, rows, read := board.Rows(f.Entry, numbers)
	var short []string
	cards := make([]prCard, 0, len(r.PRs))
	for n, pr := range r.PRs {
		at := slices.IndexFunc(rows, func(row store.ReviewRow) bool {
			return row.Number == numbers[n] && strings.EqualFold(repo, pullURL.FindStringSubmatch(pr)[1])
		})
		if at < 0 {
			if len(r.Picks) > 0 {
				short = append(short, pr+" has no row in the PR ledger")
				cards = append(cards, prCard{URL: pr})
			}
			continue
		}
		row := rows[at]
		if len(r.Picks) == 0 && (!row.ReadyForTap || row.Tapped) {
			continue
		}
		card := prCard{URL: pr, Head: row.Head, Title: row.Title, Review: board.Standing(row), Lines: Lines{row.ForUsers, board.PlanWords(row), row.Risk, row.WaitsOn}, Row: &row}
		var missing []string
		for _, field := range []struct{ said, name string }{{row.ForUsers, "what it changes for a user"}, {row.PlanRow, "the plan row"}, {row.Risk, "the risk"}} {
			if field.said == "" {
				missing = append(missing, field.name)
			}
		}
		if len(missing) > 0 {
			short = append(short, fmt.Sprintf("%s has no %s in its ledger row", pr, strings.Join(missing, ", ")))
		}
		cards = append(cards, card)
	}
	if len(r.Picks) > 0 {
		r.PRCards = cards
	}
	if len(short) == 0 {
		return r, ""
	}
	warning := strings.Join(short, "; ") + ", so its lane fixes the PR body's Summary, plan reference or Risk profile, and this reply is edited once the row has them"
	if read != nil {
		warning += fmt.Sprintf("; the PR ledger did not read: %v", read)
	}
	return r, warning
}

func (r Reply) prChild(id, pr string) map[string]any {
	named := pullURL.FindStringSubmatch(pr)
	child := map[string]any{"id": id + "-pr-" + strings.ReplaceAll(named[1], "/", "-") + "-" + named[2], "type": "pr", "repo": named[1], "number": json.Number(named[2])}
	at := slices.IndexFunc(r.PRCards, func(card prCard) bool { return card.URL == pr })
	if at < 0 {
		return child
	}
	card := r.PRCards[at]
	return board.FillPRChild(child, board.SlackPRRow{Head: card.Head, Title: card.Title, Review: card.Review, Change: card.Change, Plan: card.Plan, Risk: card.Risk, Blocks: card.Blocks, Row: card.Row})
}

func (r Reply) markdown() string {
	lines := []string{strings.TrimSpace(r.MD)}
	if len(r.Picks) > 0 {
		lines = append(lines, "")
	}
	for _, pick := range r.Picks {
		switch {
		case pick != r.Recommended:
		case r.Build == nil:
			pick += " (recommended)"
		case r.Build.None != "":
			pick += " (recommended; nothing to build: " + r.Build.None + ")"
		default:
			pick += " (recommended; built on " + r.Build.Repo + " " + r.Build.Branch + ")"
		}
		lines = append(lines, "- "+pick)
	}
	if len(r.Links)+len(r.PRs) > 0 {
		lines = append(lines, "")
	}
	for _, link := range r.Links {
		lines = append(lines, "- ["+link.Label+"]("+link.URL+")")
	}
	for _, pr := range r.PRs {
		lines = append(lines, "- "+pr)
	}
	return strings.Join(lines, "\n")
}

func (f Fanout) board(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	if len(r.Picks) == 0 {
		return f.withdraw(ctx, r)
	}
	id := "card-" + r.ID
	options := make([]map[string]any, len(r.Picks))
	for n, pick := range r.Picks {
		options[n] = map[string]any{"id": fmt.Sprintf("%s-%d", r.ID, n+1), "label": pick}
		if pick == r.Recommended {
			options[n]["recommended"] = true
			if r.Build != nil {
				options[n]["build"] = r.Build
			}
		}
	}
	lede, prose, _ := strings.Cut(strings.TrimSpace(r.MD), "\n\n")
	for _, link := range r.Links {
		prose += "\n- [" + link.Label + "](" + link.URL + ")"
	}
	children := []map[string]any{{"id": r.ID + "-choice", "type": "choice", "options": options}}
	if prose = strings.TrimSpace(prose); prose != "" {
		children = slices.Insert(children, 0, map[string]any{"id": r.ID + "-md", "type": "markdown", "md": prose})
	}
	for _, pr := range r.PRs {
		children = append(children, r.prChild(r.ID, pr))
	}
	card := map[string]any{"id": id, "type": "card", "title": r.Title, "summary": strings.Join(strings.Fields(lede), " "), "children": children}
	if posted := f.postedRef(r.ID); posted != "" {
		card["replaces"] = posted
	}
	block, err := json.Marshal(card)
	if err != nil {
		return []Outcome{{Surface: "board", Error: err.Error()}}
	}
	done, err := f.Runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "update-block", "-", "--session", f.Entry.Root(), "--round", "current"},
		Stdin:   string(block),
		Dir:     f.Entry.Checkout,
		Timeout: postTimeout,
	})
	if err != nil {
		return []Outcome{failed("board", err), {Surface: "slack", Ref: f.postedRef(r.ID), Error: "the board card did not post, so cc-present sent no Slack card"}}
	}
	posted := slackSent.FindStringSubmatch(done.Stdout)
	if posted == nil {
		return []Outcome{{Surface: "board", Ref: id}, f.leftOnTheBoard(r.ID, id)}
	}
	return []Outcome{{Surface: "board", Ref: id}, {Surface: "slack", Ref: posted[1]}}
}

func (f Fanout) leftOnTheBoard(replyID, cardID string) Outcome {
	ref := f.postedRef(replyID)
	if ref == "" {
		return Outcome{Surface: "slack", Error: "cc-present put " + cardID + " on the board and sent no Slack card"}
	}
	return Outcome{Surface: "slack", Ref: ref, Error: "cc-present updated " + cardID + " on the board and left " + ref + " as posted: he already answered that card, or it already says this"}
}

func (f Fanout) withdraw(ctx context.Context, r Reply) []Outcome {
	id := "card-" + r.ID
	was, err := f.delivered(r.ID)
	if err != nil {
		return []Outcome{{Surface: "board", Error: err.Error()}, {Surface: "slack", Error: "the earlier reply did not read, so no Slack card was edited"}}
	}
	drawn := surface(was.Surfaces, "board")
	if drawn.Surface == "" {
		return nil
	}
	copied := Outcome{Surface: "slack", Ref: surface(was.Surfaces, "slack").Ref}
	if drawn.Ref != id+withdrawn {
		if _, err := f.Runner.Run(ctx, shell.Command{
			Argv:    []string{"cc-present", "remove-block", id, "--session", f.Entry.Root(), "--reason", "edited: " + r.Title},
			Dir:     f.Entry.Checkout,
			Timeout: postTimeout,
		}); err != nil && !leftTheBoard(err) {
			copied.Error = id + " is still on the board, so its Slack card keeps its buttons"
			return []Outcome{failed("board", err), copied}
		}
	}
	gone := Outcome{Surface: "board", Ref: id + withdrawn}
	if copied.Ref == "" {
		return []Outcome{gone}
	}
	return []Outcome{gone, f.edit(ctx, r, copied, r.MD)}
}

func leftTheBoard(err error) bool {
	var exit *shell.ExitError
	return errors.As(err, &exit) && strings.Contains(exit.Stderr, alreadyGone)
}

func (f Fanout) thread(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	if !talkboard.Opened(f.Entry) {
		return nil
	}
	id := "reply-" + r.ID
	prose := strings.TrimSpace(r.MD)
	for _, link := range r.Links {
		prose += "\n- [" + link.Label + "](" + link.URL + ")"
	}
	if len(r.Picks) > 0 {
		prose += "\n\nThe question is under Needs me and in Slack."
	}
	children := make([]map[string]any, 0, 1+len(r.Blocks)+len(r.PRs))
	children = append(children, map[string]any{"id": id + "-md", "type": "markdown", "md": prose})
	for n, raw := range r.Blocks {
		var block map[string]any
		if err := json.Unmarshal(raw, &block); err != nil {
			return []Outcome{{Surface: "talk", Error: err.Error()}}
		}
		block["id"] = fmt.Sprintf("%s-b%d", id, n+1)
		if actions, ok := block["actions"].([]any); ok {
			for _, action := range actions {
				if named, ok := action.(map[string]any); ok {
					named["target"] = talkboard.Section
				}
			}
		}
		children = append(children, block)
	}
	for _, pr := range r.PRs {
		children = append(children, r.prChild(id, pr))
	}
	card := map[string]any{"id": id, "type": "card", "title": r.Title, "mirror": false, "children": children}
	if re := f.anchor(r.ID); re != "" {
		card["re"] = re
	}
	block, err := json.Marshal(card)
	if err != nil {
		return []Outcome{{Surface: "talk", Error: err.Error()}}
	}
	if _, err := f.Runner.Run(ctx, shell.Command{
		Argv:    []string{"cc-present", "update-block", "-", "--session", talkboard.Session(f.Entry), "--round", "current"},
		Stdin:   string(block),
		Dir:     f.Entry.Checkout,
		Timeout: postTimeout,
	}); err != nil {
		return []Outcome{failed("talk", err)}
	}
	return []Outcome{{Surface: "talk", Ref: id}}
}

func (f Fanout) anchor(id string) string {
	data, err := os.ReadFile(filepath.Join(f.Entry.StateDir, storeDir, id+".json"))
	if err != nil {
		return talkboard.LastNote(f.Entry)
	}
	var saved struct {
		Re string `json:"re"`
	}
	if err := json.Unmarshal(data, &saved); err != nil {
		return talkboard.LastNote(f.Entry)
	}
	return saved.Re
}

func (f Fanout) slack(ctx context.Context, r Reply, sent []Outcome) []Outcome {
	drawn := surface(sent, "slack")
	was, err := f.delivered(r.ID)
	if err != nil {
		return []Outcome{{Surface: "slack", Error: "the earlier reply did not read, so nothing was posted or edited: " + err.Error()}}
	}
	if own := was.ownMessage(r); own.Ref != "" {
		return []Outcome{f.edit(ctx, r, own, r.withPickLink(r.MD, drawn))}
	}
	if surface(was.Surfaces, "slack").Ref != "" || len(r.Picks) > 0 && drawn.Ref == "" {
		return nil
	}
	if r.Thread != "" && (drawn.Surface == "" || len(r.Picks) > 0) {
		return f.inThread(ctx, r, drawn)
	}
	if drawn.Surface != "" || r.quiet() || len(r.Links) == 0 && len(r.PRs) == 0 && r.Status == "" {
		return nil
	}
	card, err := f.statusCard(r, r.MD, "")
	if err != nil {
		return []Outcome{{Surface: "slack", Error: err.Error()}}
	}
	return f.dmStatus(ctx, append([]string{"--card", card}, r.tapFlags()...)...)
}

func (d delivery) ownMessage(r Reply) Outcome {
	if beside := surface(d.Surfaces, "slack thread"); beside.Ref != "" {
		return beside
	}
	if surface(d.Surfaces, "board").Surface != "" || len(r.Picks) > 0 {
		return Outcome{}
	}
	return surface(d.Surfaces, "slack")
}

func (f Fanout) edit(ctx context.Context, r Reply, message Outcome, body string) Outcome {
	edited := Outcome{Surface: message.Surface, Ref: message.Ref}
	card, err := f.statusCard(r, body, message.Ref)
	if err != nil {
		edited.Error = editFailed(message.Ref, err.Error())
		return edited
	}
	if done := f.dmStatus(ctx, "--card", card)[0]; done.Error != "" {
		edited.Error = editFailed(message.Ref, done.Error)
	}
	return edited
}

func editFailed(ref, why string) string {
	return "did not edit " + ref + " and posted nothing new: " + why
}

func (r Reply) withPickLink(body string, drawn Outcome) string {
	if drawn.Ref == "" || len(r.Picks) == 0 {
		return body
	}
	return body + "\n<" + drawn.Ref + "|" + pickLead + ">"
}

func (f Fanout) inThread(ctx context.Context, r Reply, drawn Outcome) []Outcome {
	body := mdLink.ReplaceAllString(r.MD, "<$2|$1>")
	for _, pr := range r.PRs {
		body += "\n" + pr
	}
	for _, link := range r.Links {
		body += "\n<" + link.URL + "|" + link.Label + ">"
	}
	posted := f.dmStatus(ctx, slices.Concat([]string{"--thread", r.Thread}, r.tapFlags(), []string{"*" + r.Title + "*\n" + body})...)
	if drawn.Surface != "" {
		posted[0].Surface = "slack thread"
	}
	return posted
}

func (r Reply) tapFlags() []string {
	if len(r.Picks) > 0 {
		return nil
	}
	flags := make([]string, 0, 2*len(r.PRs))
	for _, pr := range r.PRs {
		flags = append(flags, "--tap-pr", pr)
	}
	return flags
}

func (f Fanout) cardCopies(ctx context.Context, r Reply, sent []Outcome) []Outcome {
	drawn := surface(sent, "slack")
	cards := r.OpenPicks
	if len(r.Picks) > 0 && r.Thread != "" && drawn.Ref != "" {
		cards = append([]string{drawn.Ref}, cards...)
	}
	thread := r.Thread
	if found := slackStamp.FindStringSubmatch(drawn.Ref); thread == "" && found != nil {
		thread = found[1] + "." + found[2]
	}
	was, err := f.delivered(r.ID)
	if err != nil || was.Reply.ID != "" || thread == "" {
		return nil
	}
	copies := make([]Outcome, 0, len(cards))
	for _, card := range cards {
		done, err := f.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "card-copy", "--url", card, "--thread", thread}, Timeout: postTimeout})
		if err != nil {
			copies = append(copies, failed("card copy", err))
			continue
		}
		var posted struct {
			Permalink string `json:"permalink"`
		}
		if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Permalink == "" {
			copies = append(copies, Outcome{Surface: "card copy", Error: "cc-slack card-copy named no posted message: " + strings.TrimSpace(done.Stdout)})
			continue
		}
		copies = append(copies, Outcome{Surface: "card copy", Ref: posted.Permalink})
	}
	return copies
}

func (f Fanout) statusCard(r Reply, body, replaces string) (string, error) {
	for _, pr := range r.PRs {
		body += "\n" + pr
	}
	buttons := make([]Link, 0, len(r.Links))
	for _, link := range r.Links {
		if strings.HasPrefix(link.URL, "https://") {
			buttons = append(buttons, link)
			continue
		}
		body += "\n<" + link.URL + "|" + link.Label + ">"
	}
	key := r.ID
	if replaces != "" {
		key = ""
	}
	card, err := json.Marshal(struct {
		Key      string `json:"key,omitempty"`
		Status   string `json:"status"`
		Title    string `json:"title"`
		Body     string `json:"body"`
		Drive    string `json:"drive"`
		Links    []Link `json:"links,omitempty"`
		Replaces string `json:"replaces,omitempty"`
	}{key, cmp.Or(r.Status, "info"), r.Title, body, f.Entry.Bus(), buttons, replaces})
	return string(card), err
}

func (f Fanout) dmStatus(ctx context.Context, args ...string) []Outcome {
	done, err := f.Runner.Run(ctx, shell.Command{Argv: append([]string{"cc-slack", "dm-status"}, args...), Timeout: postTimeout})
	if err != nil {
		return []Outcome{failed("slack", err)}
	}
	var posted struct {
		Permalink string `json:"permalink"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Permalink == "" {
		return []Outcome{{Surface: "slack", Error: "cc-slack dm-status named no posted message: " + strings.TrimSpace(done.Stdout)}}
	}
	return []Outcome{{Surface: "slack", Ref: posted.Permalink}}
}

func (f Fanout) record(ctx context.Context, r Reply, _ []Outcome) []Outcome {
	content, err := json.Marshal(r)
	if err != nil {
		return []Outcome{{Surface: "record", Error: err.Error()}}
	}
	sum := sha256.Sum256(content)
	argv := []string{
		"cci", "post", "--drive", f.Entry.Bus(), "--lane", communicator, "--kind", "note", "--topic", "reply:" + r.ID,
		"--to", "owner", "--id", "reply:" + r.ID + ":" + hex.EncodeToString(sum[:6]), "--path", "-", "--json",
	}
	for _, pr := range r.PRs {
		argv = append(argv, "--pr", pr)
	}
	done, err := f.Runner.Run(ctx, shell.Command{Argv: argv, Stdin: r.Title + "\n\n" + r.markdown(), Timeout: postTimeout})
	if err != nil {
		return []Outcome{failed("record", err)}
	}
	var posted struct {
		Num string `json:"num"`
	}
	if err := json.Unmarshal([]byte(done.Stdout), &posted); err != nil || posted.Num == "" {
		return []Outcome{{Surface: "record", Error: "cci post named no record: " + strings.TrimSpace(done.Stdout)}}
	}
	return []Outcome{{Surface: "record", Ref: posted.Num}}
}

func (f Fanout) ledger(_ context.Context, r Reply, _ []Outcome) []Outcome {
	state, err := owner.Answer(f.Entry.StateDir, r.Order, r.By, r.ID, r.Routed, f.Now())
	if err != nil {
		return []Outcome{failed("ledger", err)}
	}
	return []Outcome{{Surface: "ledger", Ref: r.Order + " " + state}}
}

func (f Fanout) dashboard(_ context.Context, r Reply, sent []Outcome) []Outcome {
	path := f.stored(r.ID)
	data, err := json.MarshalIndent(delivery{r, f.Now().UTC(), f.anchor(r.ID), sent}, "", "  ")
	if err == nil {
		err = os.MkdirAll(filepath.Dir(path), 0o750)
	}
	if err == nil {
		err = os.WriteFile(path+".staged", append(data, '\n'), 0o600)
	}
	if err == nil {
		err = os.Rename(path+".staged", path)
	}
	if err != nil {
		return []Outcome{{Surface: "dashboard", Error: err.Error()}}
	}
	return []Outcome{{Surface: "dashboard", Ref: path}}
}

func (f Fanout) delivered(id string) (delivery, error) {
	var was delivery
	data, err := os.ReadFile(f.stored(id))
	if errors.Is(err, fs.ErrNotExist) {
		return was, nil
	}
	if err != nil {
		return was, err
	}
	return was, json.Unmarshal(data, &was)
}

func (f Fanout) postedRef(id string) string {
	was, err := f.delivered(id)
	if err != nil {
		return ""
	}
	return surface(was.Surfaces, "slack").Ref
}

func (f Fanout) stored(id string) string {
	return filepath.Join(f.Entry.StateDir, storeDir, id+".json")
}

func surface(sent []Outcome, name string) Outcome {
	for _, o := range sent {
		if o.Surface == name {
			return o
		}
	}
	return Outcome{}
}

func failed(surface string, err error) Outcome {
	var exit *shell.ExitError
	if errors.As(err, &exit) {
		return Outcome{Surface: surface, Error: fmt.Sprintf("%s %s exited %d: %s", exit.Argv[0], exit.Argv[1], exit.Code, strings.TrimSpace(exit.Stderr))}
	}
	return Outcome{Surface: surface, Error: err.Error()}
}

func receipt(r Reply, sent []Outcome) string {
	lines := []string{r.markdown(), "", "Reply " + r.ID + "; pass this id to edit it on every surface."}
	for _, o := range sent {
		if o.Error != "" {
			lines = append(lines, "- "+o.Surface+" FAILED: "+o.Error)
			continue
		}
		lines = append(lines, "- "+o.Surface+": "+o.Ref)
	}
	return strings.Join(lines, "\n")
}
