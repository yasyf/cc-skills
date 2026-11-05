package choices

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
)

const (
	patience     = time.Hour
	proseWindow  = 3 * time.Hour
	staleCard    = 7 * 24 * time.Hour
	communicator = "owner-relay"
	ownerAddress = "owner"
	lineChars    = 240
	listChars    = 2000
	heldShown    = 5
	heldPairs    = 2
	kindAsk      = "ask"
	kindDecide   = "decide"
	kindNote     = "note"
	kindBlocked  = "blocked"
)

var (
	cardRecord  = regexp.MustCompile(`^(?:re #\d+ )?(?:\d{1,2}:\d{2} [AP]M )?CARD\b`)
	namesCard   = regexp.MustCompile(`(?i)\bcards?\b`)
	ownerWords  = regexp.MustCompile(`(?i)\bneeds? (?:his|the owner'?s?) (?:pick|call|word|choice)\b|\bfor him to (?:pick|decide|choose)\b|\bfork is his\b|\bwait(?:s|ing)? on (?:his|the owner'?s?) (?:pick|call|word|choice)\b|\b(?:needs?|one|an) owner pick\b|\bowner pick (?:asked|needed|before|open)\b|\bplan (?:fork|gap)\b`)
	optionWords = regexp.MustCompile(`(?is)\bA \(recommended\)|\boption A\b.*\boption B\b|\boptions?:\s*(?:A|1)\b`)
	repeatWords = regexp.MustCompile(`(?i)\ba repeat\b|\bhe repeated\b|\brepeated (?:an? )?(?:order|ruling)\b|\bhas now given\b`)
	clockTime   = regexp.MustCompile(`(?i)\b\d{1,2}:\d{2}(?::\d{2})? ?(?:AM|PM)\b`)
	proseKinds  = []string{kindAsk, kindDecide, kindBlocked, "report", kindNote}
	onHisPick   = regexp.MustCompile(`(?i)\b(?:his|the owner'?s?|owner)\s+(?:\d+\s+|two\s+)?(?:picks?|answers?|taps?)\b[^.#]*\b(?:card|board)\b|\b(?:his|owner'?s?) picks?\b|\buntil he picks\b`)
	builtWork   = regexp.MustCompile(`(?i)\bat [0-9a-f]{7,40}\b|\bbuilt at\b|\bstays branch\b|\bbranch [\w./-]+ [0-9a-f]{7,40}\b|\bheads? in\b|\bcarries option\b`)
	builtNone   = regexp.MustCompile(`(?i)\bnothing built\b|\bbuilt (?:and merged )?nothing\b|\bno build\b`)
	boardRef    = regexp.MustCompile(`/p/([\w-]+)|\bboard ([0-9a-f]{32})\b`)
	unmirrored  = regexp.MustCompile(`(?i)\bnot (?:mirrored|sent|copied) to Slack\b|\bno Slack copy\b`)
)

type Finding struct {
	ID     string
	Kind   string
	To     []string
	Re     int64
	Line   string
	Covers []string
}

type Check struct {
	Drive    string
	Asks     func(ctx context.Context) ([]board.OpenAsk, error)
	Desk     func() (string, error)
	Main     func(ctx context.Context) ([]string, error)
	Owners   func(lane string) ([]string, error)
	Docs     *Docs
	Now      func() time.Time
	Patience time.Duration
	seen     map[string]time.Time
	said     map[string]bool
}

func (c *Check) Pass(ctx context.Context) error {
	if c.said == nil {
		c.said = map[string]bool{}
	}
	for _, finding := range c.Findings(ctx) {
		if c.said[finding.ID] {
			continue
		}
		_, err := client.Post(ctx, client.Draft{
			Drive: c.Drive,
			Lane:  lanes.Self,
			Kind:  finding.Kind,
			To:    finding.To,
			ID:    finding.ID,
			Re:    finding.Re,
			Text:  finding.Line,
			Force: true,
		})
		c.said[finding.ID] = err == nil
		for _, covered := range finding.Covers {
			c.said[covered] = err == nil
		}
	}
	return nil
}

func (c *Check) Findings(ctx context.Context) []Finding {
	now := c.Now()
	asks, asked := c.Asks(ctx)
	found := append(c.prose(ctx, now), c.parked(ctx, unsentCards(asks))...)
	desk, err := c.Desk()
	if err != nil {
		return found
	}
	found = append(found, c.undrawn(ctx, desk, now)...)
	if asked == nil {
		found = append(found, c.unsent(asks, desk, now)...)
	}
	return append(found, c.documented(ctx, desk, now)...)
}

func (c *Check) undrawn(ctx context.Context, desk string, now time.Time) []Finding {
	open, err := client.Open(ctx, c.Drive, kindAsk)
	if err != nil {
		return nil
	}
	var found []Finding
	for _, record := range open {
		age := now.Sub(record.At)
		if !cardRecord.MatchString(record.Text) || age < c.Patience || age > staleCard || !toBoardDesk(record.To) {
			continue
		}
		found = append(found, Finding{
			ID:   fmt.Sprintf("choice-no-card:%d", record.Seq),
			Kind: kindNote,
			To:   []string{desk},
			Re:   record.Seq,
			Line: fmt.Sprintf("NO-CARD #%d from %s is still open: %s Draw it, or answer it with why not.", record.Seq, record.Lane, excerpt(record.Text)),
		})
	}
	return found
}

func toBoardDesk(to []string) bool {
	return slices.ContainsFunc(to, func(lane string) bool { return strings.HasPrefix(lane, pass.BoardDesk) })
}

func (c *Check) unsent(asks []board.OpenAsk, desk string, now time.Time) []Finding {
	seen := make(map[string]time.Time, len(asks))
	var found []Finding
	for _, ask := range asks {
		if ask.Slack {
			continue
		}
		first, known := c.seen[ask.ID]
		if !known {
			first = now
		}
		seen[ask.ID] = first
		if now.Sub(first) < c.Patience {
			continue
		}
		found = append(found, Finding{
			ID:   "choice-no-slack:" + ask.ID,
			Kind: kindNote,
			To:   []string{desk},
			Line: fmt.Sprintf("NO-SLACK %s waits on him with no Slack copy: %s Send it.", ask.ID, excerpt(ask.Title)),
		})
	}
	c.seen = seen
	return found
}

func unsentCards(asks []board.OpenAsk) []string {
	var unsent []string
	for _, ask := range asks {
		if !ask.Slack {
			unsent = append(unsent, ask.ID)
		}
	}
	return unsent
}

func (c *Check) prose(ctx context.Context, now time.Time) []Finding {
	recent, err := client.Tail(ctx, client.Query{Drive: c.Drive, Since: now.Add(-proseWindow), Kinds: proseKinds})
	if err != nil {
		return nil
	}
	var found []Finding
	for _, record := range recent {
		var side []string
		if string(record.Kind) != kindBlocked && !board.DeskLane.MatchString(record.Lane) {
			side = c.sideBoards(ctx, record.Text)
		}
		switch {
		case record.Lane == communicator && !slices.Contains(record.To, ownerAddress) && repeatWords.MatchString(record.Text):
			found = append(found, c.repeated(ctx, record))
		case len(side) > 0:
			if finding, routed := c.offBoard(record, "board "+strings.Join(side, ", ")+" is not his main board"); routed {
				found = append(found, finding)
			}
		case untyped(record):
			found = append(found, Finding{
				ID:   fmt.Sprintf("choice-untyped:%s:%s", record.Lane, record.At.Format(time.DateOnly)),
				Kind: kindNote,
				To:   []string{record.Lane},
				Re:   record.Seq,
				Line: fmt.Sprintf("UNTYPED #%d reads as a choice for him with no card: %s If it is his, ask him with `lr ask-owner`, options recommended first. If it is yours or root's, ignore this.", record.Seq, excerpt(record.Text)),
			})
		}
	}
	return found
}

func (c *Check) parked(ctx context.Context, unsent []string) []Finding {
	open, err := client.Open(ctx, c.Drive, kindBlocked)
	if err != nil {
		return nil
	}
	var found []Finding
	for _, record := range open {
		if board.DeskLane.MatchString(record.Lane) {
			continue
		}
		if unseen := c.unseen(ctx, record, unsent); unseen != "" {
			if finding, routed := c.offBoard(record, unseen); routed {
				found = append(found, finding)
			}
			continue
		}
		if !onHisPick.MatchString(record.Text) || builtWork.MatchString(record.Text) && !builtNone.MatchString(record.Text) {
			continue
		}
		found = append(found, Finding{
			ID:   fmt.Sprintf("choice-parked:%s:%s", record.Lane, record.At.Format(time.DateOnly)),
			Kind: kindNote,
			To:   []string{record.Lane},
			Re:   record.Seq,
			Line: fmt.Sprintf("PARKED #%d waits on his pick with nothing built: %s His order m104 covers this: build the card's recommended option as a branch, no PR, and he can overturn it. A pick that deletes data, sends a message or needs his hand stays with him.", record.Seq, excerpt(record.Text)),
		})
	}
	return found
}

func (c *Check) unseen(ctx context.Context, record client.Record, unsent []string) string {
	var why []string
	if side := c.sideBoards(ctx, record.Text); len(side) > 0 {
		why = append(why, "board "+strings.Join(side, ", ")+" is not his main board")
	}
	cards := slices.DeleteFunc(namedCard.FindAllString(record.Text, -1), func(card string) bool { return !slices.Contains(unsent, card) })
	switch {
	case len(cards) > 0:
		why = append(why, strings.Join(slices.Compact(slices.Sorted(slices.Values(cards))), ", ")+" has no Slack copy")
	case unmirrored.MatchString(record.Text):
		why = append(why, "its cards have no Slack copy")
	}
	return strings.Join(why, " and ")
}

func (c *Check) sideBoards(ctx context.Context, text string) []string {
	named := boardRef.FindAllStringSubmatch(text, -1)
	if len(named) == 0 {
		return nil
	}
	main, err := c.Main(ctx)
	if err != nil {
		return nil
	}
	var side []string
	for _, ref := range named {
		if name := ref[1] + ref[2]; !slices.Contains(main, name) && !slices.Contains(side, name) {
			side = append(side, name)
		}
	}
	return side
}

func (c *Check) offBoard(record client.Record, unseen string) (Finding, bool) {
	owners, err := c.Owners(record.Lane)
	if err != nil {
		return Finding{}, false
	}
	return Finding{
		ID:   fmt.Sprintf("choice-off-board:%s:%s", record.Lane, record.At.Format(time.DateOnly)),
		Kind: kindNote,
		To:   owners,
		Re:   record.Seq,
		Line: fmt.Sprintf("OFF-BOARD #%d from %s is a defect, never a wait: %s, so he never sees it. %s asks each on the main board and in his Slack with `lr ask-owner`, then names the card-<slug> ids: %s", record.Seq, record.Lane, unseen, record.Lane, excerpt(record.Text)),
	}, true
}

func untyped(record client.Record) bool {
	if board.DeskLane.MatchString(record.Lane) || namesCard.MatchString(record.Text) {
		return false
	}
	if ownerWords.MatchString(record.Text) {
		return true
	}
	kind := string(record.Kind)
	return (kind == kindAsk || kind == kindDecide) && toHim(record.To) && optionWords.MatchString(record.Text)
}

func toHim(to []string) bool {
	return len(to) == 0 || slices.Contains(to, lanes.Root) || slices.Contains(to, communicator)
}

func (c *Check) repeated(ctx context.Context, order client.Record) Finding {
	holding := "Nothing open shares its words."
	if named := c.holding(ctx, order); len(named) > 0 {
		holding = "Open holds and blocks sharing its words: " + strings.Join(named, ", ") + "."
	}
	return Finding{
		ID:   fmt.Sprintf("choice-repeat:%d", order.Seq),
		Kind: kindAsk,
		To:   []string{lanes.Root},
		Re:   order.Seq,
		Line: fmt.Sprintf("REPEAT #%d: he gave this order again: %s %s Name the lane doing it and what holds it, unrelated work included.", order.Seq, excerpt(order.Text), holding),
	}
}

func (c *Check) holding(ctx context.Context, order client.Record) []string {
	open, err := client.Open(ctx, c.Drive, "hold", "blocked", "blocker")
	if err != nil {
		return nil
	}
	slices.SortFunc(open, func(one, other client.Record) int { return cmp.Compare(other.Seq, one.Seq) })
	var named []string
	for _, record := range open {
		if len(named) < heldShown && board.SharedWordPairs(order.Text, record.Text) >= heldPairs {
			named = append(named, fmt.Sprintf("#%d", record.Seq))
		}
	}
	return named
}

func (c *Check) documented(ctx context.Context, desk string, now time.Time) []Finding {
	if c.Docs == nil {
		return nil
	}
	var listed, covers []string
	for _, doc := range c.Docs.Open(ctx, now) {
		var fresh []string
		for _, item := range doc.Items {
			if key := "choice-doc:" + doc.Slug + ":" + item; !c.said[key] {
				fresh, covers = append(fresh, item), append(covers, key)
			}
		}
		if len(fresh) > 0 {
			listed = append(listed, fmt.Sprintf("%s (%s)", doc.Slug, strings.Join(fresh, ", ")))
		}
	}
	if len(listed) == 0 {
		return nil
	}
	list := strings.Join(listed, "; ")
	sum := sha256.Sum256([]byte(list))
	return []Finding{{
		ID:     "choice-doc:" + hex.EncodeToString(sum[:6]),
		Kind:   kindNote,
		To:     []string{desk},
		Line:   fmt.Sprintf("DOC-CHOICE design docs list choices for him that name no card: %s. Draw each, or have its author close it in the doc.", clip(list, listChars)),
		Covers: covers,
	}}
}

func excerpt(text string) string {
	return strings.TrimRight(clip(clockTime.ReplaceAllString(text, ""), lineChars), ".") + "."
}

func clip(text string, chars int) string {
	runes := []rune(strings.Join(strings.Fields(text), " "))
	if len(runes) <= chars {
		return string(runes)
	}
	return string(runes[:chars]) + "…"
}
